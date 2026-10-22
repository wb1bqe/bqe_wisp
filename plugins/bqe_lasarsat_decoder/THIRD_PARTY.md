# Protocol references

The independently implemented field parser follows the LASARsat layouts in
Libre Space Foundation's SatNOGS `ksy/lasarsat.ksy`, based on the Veronika
decoder and including contributions credited there to DL7NDR.

- Specification commit: `42b11ae8ce3022818ae60402699d5a1569dd8622`
- https://gitlab.com/librespacefoundation/satnogs/satnogs-decoders/-/blob/42b11ae8ce3022818ae60402699d5a1569dd8622/ksy/lasarsat.ksy
- Retrieved specification SHA-256: `b992bfb062036813dbeaf353d68a6a092f2db5eb5c4162d07281b2af86bcc278`
- Mission/radio listing: https://db.satnogs.org/satellite/62391/
- Mission: https://lasar.info/lasarsat

The shared audio modem uses BQE's existing G3RUH descrambling, NRZI, HDLC
and CRC-16/X-25 implementation. See `../bqe_tlm_decoder/THIRD_PARTY.md`
for that component's references. No GNU Radio or Kaitai runtime is required.
