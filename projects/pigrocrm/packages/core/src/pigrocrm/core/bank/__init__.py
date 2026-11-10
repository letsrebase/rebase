from pigrocrm.core.bank import banco_bpm_xlsx, readers

readers.register_reader(banco_bpm_xlsx.FORMAT, banco_bpm_xlsx.read_banco_bpm_xlsx)
