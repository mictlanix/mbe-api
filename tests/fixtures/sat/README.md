# SAT CFDI 4.0 schemas

SAT's schemas, used by `tests/cfdi_schema.py` to validate sealed XML (spec 027, SC-001). They were downloaded on 2026-10-25 from:

- `cfdv40.xsd`: http://www.sat.gob.mx/sitio_internet/cfd/4/cfdv40.xsd
- `tdCFDI.xsd`: http://www.sat.gob.mx/sitio_internet/cfd/tipoDatos/tdCFDI/tdCFDI.xsd
- `catCFDI.xsd.gz`: http://www.sat.gob.mx/sitio_internet/cfd/catalogos/catCFDI.xsd

Two local edits:

- The catalogue is gzipped, from 6.0 MB to 0.4 MB. libxml2 reads `.gz` files transparently.
- The two `xs:import schemaLocation` attributes in `cfdv40.xsd` point to the local files, so validation runs offline.
