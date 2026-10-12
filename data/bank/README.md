# Synthetic banking demo data

All rows in this folder are fictional and use opaque customer references. They
exist only to demonstrate the policy-impact flow; they are not customer data,
do not use real names, and must not be treated as a production data model.

`loans.csv` is the operational system-of-record fixture. `architecture.json`
is the bank topology fixture. They are deliberately separate: the graph tells
the mapper **where to look**; the read-only loan repository tells the analyser
**which records are actually affected**.
