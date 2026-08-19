# table-read

Read the record. A list of views, each one supporting a step of the design
workflow.

| Reads | Writes |
|---|---|
| `board.db` | nothing |

There is no script. Table 3 holds the SQL; run it as it stands:

```
sqlite3 -markdown builds/<build>/design/board.db "<statement>"
```

No view returns `ref_table.uuid`. It is KiCad's key and carries nothing a
person reads.

## 1 — The workflow, and the view for each step

| Step | What is done | View |
|---|---|---|
| 1 | Choose the parts the board needs | `parts` |
| 2 | Choose a manufacturer part for each | `sourcing` |
| 3 | Draw or copy the symbols | `library` |
| 4 | Capture the schematic, page by page | `page` |
| 5 | Draw or copy the footprints | `library` |
| 6 | Lay out the board | `ready` |
| 7 | Order | `sourcing` |

Footprint work is free to happen at any point between steps 2 and 6. Step 6
is the gate: `ready` names what is still missing.

`assembly` serves no single step. It is how a part and its discretes are read
together, at any point.

## 2 — The views

| View | One row per | Columns | Answers |
|---|---|---|---|
| `parts` | IPN | ipn, description, parent, count, pages, mpn | What is on the board |
| `assembly` | IPN, children under their parent | parent, ipn, description, count | What a function is built from |
| `page` | instance | page, ref, ipn, description | What goes on a sheet |
| `library` | IPN | ipn, description, symbol, footprint, source | What is drawn and what is not |
| `sourcing` | IPN and MPN | ipn, description, mpn, rank, manufacturer, datasheet | What is bought |
| `ready` | IPN | ipn, description, missing | What blocks layout |

## 3 — The SQL

| View | Statement |
|---|---|
| `parts` | `select p.ipn, p.description, p.parent, count(r.uuid) as count, group_concat(distinct r.page) as pages, (select a.mpn from aml_table a where a.ipn = p.ipn order by a.rank) as mpn from parts_table p left join ref_table r using (ipn) group by p.ipn order by p.ipn;` |
| `assembly` | `select coalesce(p.parent, p.ipn) as parent, p.ipn, p.description, count(r.uuid) as count from parts_table p left join ref_table r using (ipn) group by p.ipn order by parent, p.parent is null desc, p.ipn;` |
| `page` | `select r.page, r.ref, r.ipn, p.description from ref_table r join parts_table p using (ipn) order by r.page, r.ref;` |
| `library` | `select ipn, description, symbol, footprint, source from parts_table order by symbol is not null, footprint is not null, ipn;` |
| `sourcing` | `select a.ipn, p.description, a.mpn, a.rank, m.manufacturer, m.datasheet from aml_table a join parts_table p on p.ipn = a.ipn join mpn_table m on m.mpn = a.mpn order by a.ipn, a.rank;` |
| `ready` | `select p.ipn, p.description, trim(case when not exists (select 1 from aml_table a where a.ipn = p.ipn) then 'mpn ' else '' end \|\| case when p.symbol is null then 'symbol ' else '' end \|\| case when p.footprint is null then 'footprint' else '' end) as missing from parts_table p where missing <> '' order by p.ipn;` |
