from pigrocrm.core.bank import banco_bpm_xlsx, camt053, readers

readers.register_reader(banco_bpm_xlsx.FORMAT, banco_bpm_xlsx.read_banco_bpm_xlsx)
readers.register_reader(camt053.FORMAT, camt053.read_camt053)
