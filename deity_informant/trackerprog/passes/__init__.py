"""The tuneprog-to-trackerprog pipeline as compiler passes (L1-L6).

One module a level, each with one interface: a level object in, a level object
out, and :func:`~.ir.validate`, which renders both and compares their write
lists.  From L2 on a level object is itself a trackerprog, so every pass is
translation-validated against the unchanged player.
"""
