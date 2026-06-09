OT Lab v2 backup snapshot
Timestamp: 20260526_185856

Files:
- fuxa_project.json           -> FUXA import/export JSON (views + devices + tags)
- project.fuxap.db            -> FUXA full project database
- openplc.db                  -> OpenPLC runtime database
- active_program              -> active ST filename reference
- openplc_active_program.st   -> active ST source file (if found)
- openplc_variables.csv       -> OpenPLC generated variable map

Restore notes:
1) FUXA JSON restore:
   curl -X POST http://localhost:1881/api/project \
     -H "Content-Type: application/json" \
     --data-binary @fuxa_project.json

2) OpenPLC logic restore:
   Upload openplc_active_program.st (or your chosen .st) in OpenPLC web UI,
   then compile and start PLC.
