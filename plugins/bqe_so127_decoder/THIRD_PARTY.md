# Protocol and fixture attribution

Telemetry layouts, calibration equations and scrambler behavior are adapted
from AMSAT-EA's HADES-SA / SpinnyONE decoder, with credit to AMSAT-EA and
scrambler author Gabriel Otero Perez. The ten unchanged sample packet files
in `tests/fixtures/` are from the same source:

- https://github.com/AMSAT-EA/HADES-SA_SpinnyONE/tree/ce1e09d2112a65954f9826b856deea9afe1e171c/byte_version
- https://www.amsat-ea.org/proyectos/
- https://www.amsat-ea.org/app/download/14191564/AMSAT%2BEA%2B-%2BHADES-SA_SpinnyONE%2BTransmissions%2BDescription.pdf

AMSAT-EA publishes these materials under Creative Commons Attribution 4.0
International: https://creativecommons.org/licenses/by/4.0/ . This work ports
their protocol interpretation to Python and adds streaming audio reception,
validation, structured records, bounded framing and BQE integration. Fixtures
remain unmodified. No operator executable is bundled or required.

The existing BQE capture service and symbol clock are reused under the
project's GPL-3.0-or-later terms. New integration and receiver code follows the
project license; the attribution above applies to adapted protocol material.
