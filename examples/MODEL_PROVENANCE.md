# Included models

These models were learned from actual CPython native objects using opaque action labels. Public labels were then renamed through a one-to-one identity map for readability; every expression and data-state rule is unchanged. The UTF16 latent-evidence action identity was renamed consistently. Public `check` receipts measure the renamed models against the declared native paths.

Mappings: text a1/write,a2/read,a3/seek,a4/value,a5/position; sequence s1/append,s2/extend,s3/pop,s4/popleft,s5/reverse,s6/rotate; encoded e1/write,e2/rawSeek,e3/truncate,e4/textSeek,e5/bytes,e6/position.

Supplied API declarations and grammar/codec semantics are priors. A public name does not select an effect implementation. A successful finite check is not general behavioral equivalence.
