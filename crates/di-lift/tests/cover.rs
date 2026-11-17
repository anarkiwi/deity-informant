//! Covered checks (`cover`): a player's machine-checked loads into its song's data
//! block, a check a dominating check of the same address form makes left out.

#[allow(dead_code)]
mod common;
use common::{entries, image};
use di_lift::{lift_with, Facts, Opts, Program};

/// The song: data bytes at `$1100` past the play routine at `$1000`.
const SONG: u16 = 0x1100;

/// The player lift of play routine `code` at `$1000` and the song, the host's init leaving
/// `free` and holding `held`.
fn play(code: &[u8], free: Vec<(u16, Vec<u8>)>, held: Vec<u16>) -> Program {
    let song: Vec<u8> = (0..0x80).collect();
    let img = image(&[(0x1000, code), (SONG, &song)]);
    let opts = Opts {
        entry_sp: Some(0xfd),
        loaded: Some((0x1000, SONG as u32 + song.len() as u32)),
        player: true,
        free,
        held,
        ..Opts::default()
    };
    lift_with(
        &img,
        &entries(&[0x1000]),
        &[(0xc000, 0xc100)],
        &Facts::default(),
        opts,
    )
}

/// The widths `k` of the widened checks whose object serves the load at `pc`.
fn widened(p: &Program, pc: u16) -> Vec<u16> {
    (p.slack.iter())
        .filter(|(j, _)| p.objects[*j as usize].2.contains(&pc))
        .map(|(_, (_, _, k))| *k)
        .collect()
}

/// A pair init leaves pointing into the song, read at `Y = 0` then `Y = 3`: the first read's
/// check is widened over both offsets and the second read needs none of its own.
#[test]
fn a_dominating_check_covers_a_later_offset_of_its_pair() {
    // LDY #0; LDA ($FB),Y; STA $C000; LDY #3; LDA ($FB),Y; STA $C001; RTS
    let code = [
        0xa0, 0x00, 0xb1, 0xfb, 0x8d, 0x00, 0xc0, 0xa0, 0x03, 0xb1, 0xfb, 0x8d, 0x01, 0xc0, 0x60,
    ];
    let p = play(
        &code,
        vec![(0xfb, vec![0x00, 0x40]), (0xfc, vec![0x11, 0x30])],
        vec![],
    );
    let data: Vec<u16> = p.data.iter().map(|d| d.0).collect();
    assert_eq!(data, [0x1002, 0x1009]);
    assert_eq!(p.machine, [0x1002]);
    assert_eq!(widened(&p, 0x1002), [3]);
    assert!(widened(&p, 0x1009).is_empty());
}

/// Two distinct pairs init leaves, neither stored by the routine, are values of distinct
/// bytes: each read keeps its own check, and none is widened.
#[test]
fn distinct_pairs_undefined_since_the_entry_share_no_check() {
    // LDY #0; LDA ($FB),Y; STA $C000; LDA ($FD),Y; STA $C001; RTS
    let code = [
        0xa0, 0x00, 0xb1, 0xfb, 0x8d, 0x00, 0xc0, 0xb1, 0xfd, 0x8d, 0x01, 0xc0, 0x60,
    ];
    let free = vec![
        (0xfb, vec![0x00, 0x10]),
        (0xfc, vec![0x11, 0x30]),
        (0xfd, vec![0x40, 0x50]),
        (0xfe, vec![0x11, 0x30]),
    ];
    let p = play(&code, free, vec![]);
    assert_eq!(p.machine, [0x1002, 0x1007]);
    assert!(p.slack.is_empty());
}

/// `LDY $FB; LDA $1100,Y; STA $C000; LDA $1140,Y; STA $C001; RTS`: two absolute indexed loads
/// of one index at image bases `$40` apart.
const ABS_Y: [u8; 15] = [
    0xa4, 0xfb, 0xb9, 0x00, 0x11, 0x8d, 0x00, 0xc0, 0xb9, 0x40, 0x11, 0x8d, 0x01, 0xc0, 0x60,
];

/// Absolute operands init holds are its data, no constants: the distance between the image's
/// bases is no width either load's check may take, and each keeps its own.
#[test]
fn held_absolute_operands_are_no_constant_bases() {
    let held = vec![0x1003, 0x1004, 0x1009, 0x100a];
    let p = play(&ABS_Y, vec![(0xfb, vec![0x00])], held);
    assert_eq!(p.machine, [0x1002, 0x1008]);
    assert!(!p.slack.iter().any(|(_, (_, _, k))| *k == 0x40));
}

/// The same loads with constant operands: the first load's check is widened by the distance
/// between their bases and covers the second.
#[test]
fn constant_absolute_operands_are_one_form_apart_by_their_bases() {
    let p = play(&ABS_Y, vec![(0xfb, vec![0x00])], vec![]);
    assert_eq!(p.machine, [0x1002]);
    assert_eq!(widened(&p, 0x1002), [0x40]);
}
