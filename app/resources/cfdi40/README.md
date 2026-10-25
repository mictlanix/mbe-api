# CFDI 4.0 original-string stylesheet

SAT's stylesheet for the CFDI 4.0 original string ("cadena original") in its XSLT 1.0 form, with the complement stylesheets it includes. This copy runs offline in lxml (spec 027, research R3).

**Source:** `mictlanix/cfdlib`, `src/CFDv40/Resources/`, commit `aa545a96821bdedcd1eda4dbe07d0d68f1050c00`. This is the copy the current system seals with.

- **Main file:** `cadena_original_4_0.xslt`.
- **The rest:** the 30 files it includes. Nothing else from that directory is copied.
- **One edit:** the include `divisas.xslt` becomes `Divisas.xslt`, the file's real name. cfdlib loads includes from embedded resources, which ignore case; a Linux file system does not.

Replace these files only from a newer cfdlib or SAT release, and rerun `tests/unit/test_cfdi_invoice.py`.
