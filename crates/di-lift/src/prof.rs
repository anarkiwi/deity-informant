//! Wall time per named phase of the lift, summed over its runs since the last [`take`].

use std::sync::Mutex;
use std::time::Instant;

static TIMES: Mutex<Vec<(&'static str, f64, u32)>> = Mutex::new(Vec::new());

/// A run of phase `0`: its wall time is added when it drops.
pub struct Span(&'static str, Instant);

/// A run of phase `name` from now until the span drops.
pub fn span(name: &'static str) -> Span {
    Span(name, Instant::now())
}

impl Drop for Span {
    fn drop(&mut self) {
        let dt = self.1.elapsed().as_secs_f64();
        let mut times = TIMES.lock().unwrap_or_else(|e| e.into_inner());
        match times.iter_mut().find(|e| e.0 == self.0) {
            Some(e) => {
                e.1 += dt;
                e.2 += 1;
            }
            None => times.push((self.0, dt, 1)),
        }
    }
}

/// The phases' `(name, seconds, runs)` in the order first ended, cleared.
pub fn take() -> Vec<(&'static str, f64, u32)> {
    std::mem::take(&mut *TIMES.lock().unwrap_or_else(|e| e.into_inner()))
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn spans_sum_per_phase_until_taken() {
        for _ in 0..2 {
            let _s = span("prof.test");
            std::thread::sleep(std::time::Duration::from_millis(5));
        }
        let got = take();
        let (_, secs, runs) = got.iter().find(|e| e.0 == "prof.test").copied().unwrap();
        assert_eq!(runs, 2);
        assert!(secs >= 0.01);
        assert!(take().iter().all(|e| e.0 != "prof.test"));
    }
}
