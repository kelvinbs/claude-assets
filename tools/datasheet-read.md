# datasheet-read

Look at the datasheet file named in `parts_table.datasheet`. Populate the
fields of T1 in the part file, `design/parts/<IPN>-<name>.json`. A field
the datasheet does not state is left absent, never guessed.

The part file is the cache. A key present is not re-read. The PDF is
opened only for a key absent. Delete the key to force a read.

`pins` holds every pin the package has, 1 to N, none missing, none
repeated, an exposed pad numbered after the last. The count is the
datasheet's pin count. A `pins` that fails this is not written.

**T1 — Part file keys**

| # | Key | Holds | Stage |
|---|---|---|---|
| 1 | `pins` | `[[number, name, type, side], ...]`. `type` a KiCad electrical type. `side` L R T B | 3 |
| 2 | `pages.pins` | page numbers the pins came from | 3 |
| 3 | `package` | the datasheet's package code | 5 |
| 4 | `package_dims` | body length, width, height, pitch, pad count, exposed-pad size. mm | 5 |
| 5 | `pages.package` | page numbers the package came from | 5 |
| 6 | `units` | pin numbers per unit, multi-unit parts only | 3 |
| 7 | `symbol_donor` | written by the copy, not here | 3 |
| 8 | `footprint_donor` | written by the copy, not here | 5 |
