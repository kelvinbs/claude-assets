# table-read

Read the record. A list of views, each one supporting a step of the design
workflow.

| Reads | Writes |
|---|---|
| `board.db` | nothing |

There is no script. Table 3 holds the SQL; run it as it stands:

```
sqlite3 -markdown <board-dir>/board.db "<statement>"
```

No view returns `ref_table.uuid`. It is KiCad's key and carries nothing a
person reads.

## 1 — The workflow, and the view for each step

| Step | What is done | View |
|---|---|---|
| 1 | Choose the parts the board needs | `parts_view` |
| 2 | Choose a manufacturer part for each | `sourcing_view` |
| 3 | Draw or copy the symbols | `library_view` |
| 4 | Capture the schematic, page by page | `page_view` |
| 5 | Draw or copy the footprints | `library_view` |
| 6 | Lay out the board | `ready_view` |
| 7 | Order | `sourcing_view` |

Footprint work is free to happen at any point between steps 2 and 6. Step 6
is the gate: `ready_view` names what is still missing.

`assembly_view` serves no single step. It is how a part and its discretes are read
together, at any point.

## 2 — The views

| View | One row per | Columns | Answers |
|---|---|---|---|
| `parts_view` | IPN | name, ipn, description, count, pages, mpn | What is on the board |
| `assembly_view` | IPN, children under their parent | parent, ipn, description, count | What a function is built from |
| `page_view` | instance | page, ref, ipn, description | What goes on a sheet |
| `library_view` | IPN | ipn, description, symbol, footprint, source | What is drawn and what is not |
| `sourcing_view` | IPN | name, ipn, description, mpn, manufacturer, datasheet | What is bought |
| `ready_view` | IPN | ipn, description, missing | What blocks layout |

## 3 — The SQL

| View | Statement |
|---|---|
| `parts_view` | `select p.name, p.ipn, p.description, count(r.uuid) as count, group_concat(distinct r.page) as pages, p.mpn from parts_table p left join ref_table r using (ipn) group by p.ipn order by p.ipn;` |
| `assembly_view` | `select coalesce(pp.ipn, p.ipn) as parent, p.name, p.ipn, p.description, count(r.uuid) as count from parts_table p left join ref_table r using (ipn) left join ref_table rp on rp.uuid = r.parent left join parts_table pp on pp.ipn = rp.ipn group by p.ipn, pp.ipn order by parent, pp.ipn is null desc, p.ipn;` |
| `page_view` | `select r.page, r.ref, p.name, r.ipn, p.description from ref_table r join parts_table p using (ipn) order by r.page, r.ref;` |
| `library_view` | `select name, ipn, description, symbol, footprint, source from parts_table order by symbol is not null, footprint is not null, ipn;` |
| `sourcing_view` | `select p.name, p.ipn, p.description, p.mpn, p.manufacturer, p.datasheet from parts_table p where p.mpn is not null order by p.ipn;` |
| `ready_view` | `select p.name, p.ipn, p.description, trim(case when p.mpn is null then 'mpn ' else '' end \|\| case when p.symbol is null then 'symbol ' else '' end \|\| case when p.footprint is null then 'footprint' else '' end) as missing from parts_table p where missing <> '' order by p.ipn;` |
