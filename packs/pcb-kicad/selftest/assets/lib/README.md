# KiCad library entries for the baseline

These are the stock library entries the baseline board and schematic were drawn
from, and nothing else:

- `Connector_PinHeader_2.54mm.pretty/PinHeader_1x04_P2.54mm_Vertical.kicad_mod`
  and `Package_TO_SOT_SMD.pretty/SOT-23-6.kicad_mod`, copied byte for byte from
  the kicad-footprints 7.0.11 package;
- `power.kicad_sym` (GND, PWR_FLAG, VBUS), `Connector_Generic.kicad_sym`
  (Conn_01x04) and `Power_Protection.kicad_sym` (ESDA6V1BC6), each the
  kicad-symbols 7.0.11 library cut down to those symbols under its own header.

`../make_libraries.py` made them, and says how. They are licensed CC-BY-SA 4.0
with the KiCad libraries' exception for designs that use them; the copyright is
the KiCad library contributors'.

They are here so the known-good DRC and ERC controls read the same library on
every host. The baseline's tables (`../fp-lib-table`, `../sym-lib-table`) name
them as `${KIPRJMOD}/lib/...`, and the gates stage them with the board. With
tables that named the host's stock library, the controls read whatever library
the host had installed: under KiCad 10.0.8, whose own library is newer, the
known-good board read three `lib_footprint_mismatch` warnings.
