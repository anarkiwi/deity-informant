# Certified facts

`cgen.translate(..., certified=True)` (and `cgen.lift(..., certified=True)`) proves what
every run of the translation does, and the C generator reads those facts in place of the
static analysis where they answer the same question. `deity_informant.certified` holds them.

## What is proven

For each entry but init, called again and again with `A = 0` from each state init leaves for
the declared songs (or, with no init, from the image):

- `jennings.coverage.certificate.certify` gives a certificate: the feed locations, their
  preperiod and period, and every data access `(pc, load or store) -> addresses` and edge
  `(pc, next)` of the calls up to the feed's repeat;
- `jennings.coverage.certificate.verify` replays it from the certificate alone and checks
  that the feed repeats byte for byte, that every call is tainted, and that nothing outside
  the feed set taints an address, a control transfer or the feed set. Every later call then
  repeats one of the calls covered, so those accesses and edges are every one play takes.

Init, called once per declared song, is proven by its runs over every value its device probes
may read (`postinit.forks`): each run's accesses, instructions and edges. The states those runs
leave are certified as play's starting states too, as are the machine's.

`Facts` holds the union: `accesses`, `executed`, `edges`, how each entry is proven
(`proven`: `"certificate"` or `"runs"`) and the certificates with their verdicts.
`info["certified"]` reports the counts, the verdicts and the assumptions.

## What is refused

`certified.Unproven` is raised, with no fallback to an unverified run, when:

- `certify` finds no cover point within `certified.LIMIT` calls (an `Open`), or a call does not
  return;
- the verdict is not ok, or it leans on an excluded location (`Verdict.assumed`);
- play loads a volatile address (an input the certificate's model does not have);
- init is translated but a declared song's runs are missing or not exact.

`certified=True` needs `overrun="trap"`: the facts are the machine's runs, which the zero rule's
reads leave.

## Assumptions

`certified.ASSUMED` lists what the facts rest on beside the checks:

- the native 6502 core is the machine, and its flow tables name every input of each output;
- play has no input: it loads no volatile address, the stack page and the volatile ranges carry
  no state between calls, and the host calls it with `A = 0` only;
- init's probes read only values of the device model's sets.

## Consumers

The C generator (`printer.Module.proven_set`) gives an access the addresses its instructions
(every copy's) take, of its kind, met with its address interval. Where an access has them:

- an object check whose instructions never run, or whose addresses past the zero page all lie
  in the check's object, prints no check and leaves `info["unbounded"]`;
  `info["traced_bounds"]` lists those instructions ([cgen](cgen.md#certified-facts));
- the cells it touches, which promotion classifies, are its addresses, not the hull of its
  static interval;
- it reaches the one region or held span its addresses lie in unchecked.

The facts are those of the declared songs: two songs of one player can lift to different `.c`
where their runs differ.
