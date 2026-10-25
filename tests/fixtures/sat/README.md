# SAT CFDI 4.0 schemas

SAT's schemas, used by `tests/cfdi_schema.py` to validate sealed XML (spec 027, SC-001). They were downloaded on 2026-10-25 from:

- `cfdv40.xsd`: http://www.sat.gob.mx/sitio_internet/cfd/4/cfdv40.xsd
- `tdCFDI.xsd`: http://www.sat.gob.mx/sitio_internet/cfd/tipoDatos/tdCFDI/tdCFDI.xsd
- `catCFDI.xsd.gz`: http://www.sat.gob.mx/sitio_internet/cfd/catalogos/catCFDI.xsd

Two local edits:

- The catalogue is gzipped, from 6.0 MB to 0.4 MB. libxml2 reads `.gz` files transparently.
- The two `xs:import schemaLocation` attributes in `cfdv40.xsd` point to the local files, so validation runs offline.

## Added for spec 028

- `Pagos20.xsd`: http://www.sat.gob.mx/sitio_internet/cfd/Pagos/Pagos20.xsd
- `catPagos.xsd`: http://www.sat.gob.mx/sitio_internet/cfd/catalogos/Pagos/catPagos.xsd

Both were downloaded on 2026-10-25, and their `schemaLocation` imports point to the local files.

`cfdi40_with_complements.xsd` is a local wrapper, not a SAT file. It imports `cfdv40.xsd` and `Pagos20.xsd` into one schema set, because `cfdv40.xsd` validates `cfdi:Complemento` strictly and needs the complement's schema loaded beside it.
