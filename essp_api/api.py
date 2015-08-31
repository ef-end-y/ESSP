import serial
import logging
import time


class ESSPException(Exception):
    pass


class NullHandler(logging.Handler):
    def emit(self, record):
        pass


class SerialNull(object):
    @staticmethod
    def write(*args, **kwargs):
        return

    @staticmethod
    def read(*args, **kwargs):
        return ''

    @staticmethod
    def inWaiting(*args, **kwargs):
        return 0


class EsspApi(object):
    READ_NOTE = 0xEF  # 239
    CREDIT_NOTE = 0xEE  # 238
    FRAUD_ATTEMPT = 0xE6  # 230
    NOTE_CLEARED_FROM_RESET = 0xE1
    NOTE_CLEARED_INTO_CASHBOX = 0xE2
    NOTE_REJECTING = 0xED  # 237
    NOTE_REJECTED = 0xEC  # 236
    DISABLED = 0xE8  # 232
    STACKER_FULL = 0xE7  # 231
    SLAVE_RESET = 0xF1  # right after booting up
    STACKING = 0xCC  # 204
    STACKED = 0xEB  # 235
    CASH_BOX_REMOVED = 0xE3
    CASH_BOX_REPLACED = 0xE4
    COMMAND_NOT_PROCESSED = 0xF5  # 245
    UNKNOWN_COMMAND = 0xF2  # 242

    two_parameters_status = (READ_NOTE, CREDIT_NOTE, FRAUD_ATTEMPT, NOTE_CLEARED_FROM_RESET, NOTE_CLEARED_INTO_CASHBOX)

    _logger = None
    _serialport = None
    _serial = None
    _id = None
    _sequence = True
    _serialnull = None

    def __init__(self, serialport='/dev/ttyUSB0', essp_id=0, logger_handler=None, verbose=False):
        self._logger = logging.getLogger(__name__)
        self._logger.addHandler(logger_handler if logger_handler else NullHandler())
        self._logger.setLevel(logging.DEBUG if verbose else logging.INFO)
        self._serialport = serialport
        self._serialnull = SerialNull()
        self._id = essp_id
        self._logger.info('[ESSP] Start')

    @property
    def _device(self):
        if self._serial:
            return self._serial
        try:
            self._serial = serial.Serial(self._serialport, 9600)
        except Exception as e:
            self._logger.error('[ESSP] %s' % e)
            time.sleep(10)
            return self._serialnull
        return self._serial

    def get_logger(self):
        return self._logger

    def reset(self):
        self._logger.info('[ESSP][cmd] Reset')
        return self._simple_cmd(1)

    def set_inhibits(self, low_channels, high_channels):
        self._logger.info('[ESSP][cmd] Set inhibits')
        return self._simple_cmd([2, low_channels, high_channels])

    def display_on(self):
        self._logger.info('[ESSP][cmd] Display on')
        result = self._send(3)
        return result

    def display_off(self):
        self._logger.info('[ESSP][cmd] Display off')
        result = self._send(4)
        return result

    def setup_request(self):
        self._logger.info('[ESSP][cmd] Setup request')
        try:
            result = self._send(5)
            channels = int(result[11])
            return {
                'unit type': result[0],
                'firmware': ''.join([chr(c) for c in result[1:5]]),
                'country': ''.join([chr(c) for c in result[5:8]]),
                'multiplier': result[8]*0x10000 + result[9]*0x100 + result[10],
                'channels': channels,
                'values': result[12:12+channels],
                'security': result[12+channels:12+channels*2],
                'real multiplier': self._list_to_int(result[12+channels*2:15+channels*2]),
                'protocol': result[15+channels*2],
            }
        except (ESSPException, IndexError):
            return {}

    def host_protocol_version(self, host_protocol):
        try:
            self._send([6, host_protocol])
        except ESSPException:
            return False
        return True

    def poll(self):
        self._logger.debug('[ESSP][cmd] Polling ...')
        poll_data = []
        try:
            result = self._send(7)
        except ESSPException:
            return poll_data

        result.reverse()
        while len(result):
            c = result.pop()
            if c in self.two_parameters_status:
                param = result.pop()
            else:
                param = None
            poll_data.append({
                'status': c,
                'param': param
            })
        return poll_data

    def reject_note(self):
        self._logger.info('[ESSP][cmd] Reject the note')
        return self._simple_cmd(8)

    def disable(self):
        self._logger.info('[ESSP][cmd] Disable the device')
        return self._simple_cmd(9)

    def enable(self):
        self._logger.info('[ESSP][cmd] Enable the device')
        return self._simple_cmd(0xA)

    def serial_number(self):
        """
            Returns serial number
        """
        try:
            return self._send(0xC)
        except ESSPException:
            return 'ERROR'

    def unit_data(self):
        """
        Returns array:
            Unit-Type (0 = BNV)
            Firmware-Version
            Country-Code
            Value-Multiplier
            Protocol-Version
        """
        try:
            result = self._send(0xD)
            unittype = result[0]
            fwversion = ''.join([chr(c) for c in result[1:5]])
            country = ''.join([chr(c) for c in result[5:8]])
            valuemulti = self._list_to_int(result[8:11])
            protocol = result[11]
            unit_data = [unittype, fwversion, country, valuemulti, protocol]
        except (ESSPException, IndexError):
            return [0, '', '', 0, 0]
        return unit_data

    def channel_values(self):
        """
            Returns the real values of the channels
        """
        try:
            result = self._send('e')
            channels = result[0]
        except (ESSPException, IndexError):
            return []
        unitdata = self.unit_data()
        return [result[1 + i] * unitdata[3] for i in range(0, channels)]

    def channel_security(self):
        # Returns the security settings of all channels
        # 1 = Low Security
        # 2 = Std Security
        # 3 = High Security
        # 4 = Inhibited
        try:
            result = self._send(0xF)
            return result[1:1+result[0]]
        except (ESSPException, IndexError):
            return []

    def sync(self):
        self._logger.info('[ESSP][cmd] Sync')
        self._sequence = False
        return self._simple_cmd(0x11)

    def last_reject(self):
        # Get reson for latest rejected banknote
        # 0x00 = Note Accepted
        # 0x01 = Note length incorrect
        # 0x02 = Reject reason 2
        # 0x03 = Reject reason 3
        # 0x04 = Reject reason 4
        # 0x05 = Reject reason 5
        # 0x06 = Channel Inhibited
        # 0x07 = Second Note Inserted
        # 0x08 = Reject reason 8
        # 0x09 = Note recognised in more than one channel
        # 0x0A = Reject reason 10
        # 0x0B = Note too long
        # 0x0C = Reject reason 12
        # 0x0D = Mechanism Slow / Stalled
        # 0x0E = Striming Attempt
        # 0x0F = Fraud Channel Reject
        # 0x10 = No Notes Inserted
        # 0x11 = Peak Detect Fail
        # 0x12 = Twisted note detected
        # 0x13 = Escrow time-out
        # 0x14 = Bar code scan fail
        # 0x15 = Rear sensor 2 Fail
        # 0x16 = Slot Fail 1
        # 0x17 = Slot Fail 2
        # 0x18 = Lens Over Sample
        # 0x19 = Width Detect Fail
        # 0x1A = Short Note Detected
        try:
            return self._send(0x17)[0]
        except (ESSPException, IndexError):
            return 0

    def hold(self):
        self._logger.info('[ESSP][cmd] Hold')
        return self._simple_cmd(0x18)

    def enable_higher_protocol(self):
        # Enables functions from implemented with version >= 3
        return self._simple_cmd(0x19)

    def _simple_cmd(self, cmd):
        try:
            self._send(cmd)
        except ESSPException:
            return False
        return True

    def _getseq(self):
        self._sequence = not self._sequence
        return '%02x' % (self._id | 0x80 if self._sequence else 0)

    @staticmethod
    def _crc(command):
        seed = 0xffff
        poly = 0x8005
        crc = seed
        for cmd in command:
            crc ^= int(cmd, 16) << 8
            for j in range(0, 8):
                if crc & 0x8000:
                    crc = ((crc << 1) & 0xffff) ^ poly
                else:
                    crc <<= 1
        return [('%02x' % (crc & 0xff)).lower(), ('%02x' % ((crc >> 8) & 0xff)).lower()]

    def _send(self, commands):
        if isinstance(commands, (list, tuple)):
            data = commands[:]
        else:
            data = [commands]
        data = ['%02x' % c if isinstance(c, int) else c for c in data]
        data.insert(0, '%02x' % len(data))
        data.insert(0, self._getseq())

        data += self._crc(data)

        request = ['7f']
        for c in data:
            request.append('%02x' % int(c, 16))
            if c == '7f':
                request.append(c)

        self._logger.debug('[ESSP] SEND: ' + ' '.join(request))

        self._send_2tries(''.join(request).decode('hex'))

        return self._read()

    def _send_2tries(self, data):
        for i in (0, 1):
            try:
                self._device.write(data)
            except Exception:
                self._serial = None
            else:
                return
        raise ESSPException

    def _read_chars(self, count=1):
            return [ord(c) for c in self._device.read(count)]

    def _read(self):
        response = []
        step = 0
        waiting_chars = 1
        timeout = time.time() + 1.1
        while time.time() < timeout:
            ready_chars = self._device.inWaiting()
            if ready_chars < waiting_chars:
                time.sleep(0.01)
                continue
            chars = self._read_chars(waiting_chars)

            if step == 0:
                if chars[0] != 0x7f:
                    continue
                response = chars
                waiting_chars = 2
                step = 1
                continue

            response += chars

            if step == 1:
                waiting_chars = chars[1] + 2
                step = 2
                continue

            response = [('%02x' % c).lower() for c in response]

            self._logger.debug('[ESSP] RECV:  ' + ' '.join(response))

            crc = self._crc(response[1:-2])
            if crc != response[-2:]:
                self._logger.warn('[ESSP] RECV:  ' + ' '.join(response))
                self._logger.warn('[ESSP] Failed to verify crc: ' + str(crc))

            if len(response) < 6:
                raise ESSPException()
            response = [int(c, 16) for c in response]
            if response[3] != 0xf0:
                self._logger.info('[ESSP] Error 0x%02x' % response[3])
                raise ESSPException()
            return response[4:-2]

        self._serial = None
        raise ESSPException()

    @staticmethod
    def easy_inhibit(acceptmask):
        bitmask = int('00000000', 2)
        for i, val in enumerate(acceptmask):
            if val:
                bitmask += pow(2, i)
        return '%02x' % bitmask

    @staticmethod
    def _list_to_int(data):
        res = 0
        for d in data:
            res = res * 0x100 + d
        return res
