//! Work run at once on scoped threads made per call: no pool outlives a call, so a process
//! forked from one that ran them (a test's worker) runs them too.

use std::sync::atomic::{AtomicUsize, Ordering};

/// `(a(), b())`, `b` on a thread of its own.
pub fn join<A: Send, B: Send>(
    a: impl FnOnce() -> A + Send,
    b: impl FnOnce() -> B + Send,
) -> (A, B) {
    std::thread::scope(|s| {
        let hb = s.spawn(b);
        let ra = a();
        (
            ra,
            hb.join().unwrap_or_else(|e| std::panic::resume_unwind(e)),
        )
    })
}

/// `f` of each of `items`, in order, on up to one thread per core.
pub fn map<T: Sync, R: Send>(items: &[T], f: impl Fn(&T) -> R + Sync) -> Vec<R> {
    let n = std::thread::available_parallelism()
        .map_or(1, |n| n.get())
        .min(items.len());
    if n <= 1 {
        return items.iter().map(f).collect();
    }
    let next = AtomicUsize::new(0);
    let mut got: Vec<(usize, R)> = std::thread::scope(|s| {
        let work = || {
            let mut out = Vec::new();
            loop {
                let i = next.fetch_add(1, Ordering::Relaxed);
                let Some(x) = items.get(i) else { break out };
                out.push((i, f(x)));
            }
        };
        let hs: Vec<_> = (1..n).map(|_| s.spawn(work)).collect();
        let mut all = work();
        for h in hs {
            all.extend(h.join().unwrap_or_else(|e| std::panic::resume_unwind(e)));
        }
        all
    });
    got.sort_unstable_by_key(|e| e.0);
    got.into_iter().map(|e| e.1).collect()
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn map_keeps_order_and_join_runs_both() {
        let xs: Vec<u32> = (0..1000).collect();
        assert_eq!(
            map(&xs, |&x| x * x),
            xs.iter().map(|x| x * x).collect::<Vec<_>>()
        );
        assert_eq!(map(&[] as &[u32], |&x| x), Vec::<u32>::new());
        assert_eq!(map(&[7u32], |&x| x + 1), vec![8]);
        assert_eq!(join(|| 1, || "b"), (1, "b"));
    }
}
