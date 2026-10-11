"""Build fingerprints, encoded input semantics and reversible transactions."""
import hashlib
from pathlib import Path
import struct
import sys
import unittest
from unittest import mock

from capstone import Cs, CS_ARCH_ARM, CS_MODE_ARM
from capstone.arm import ARM_OP_IMM, ARM_OP_REG

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))
from kiu.experimental import patch_button_dash as dash


class Guest:
    def __init__(self):
        self.memory = {}
        self.writes = []
        for address, raw, _ in dash.GUARDS:
            self.load(address, raw)
        for site in dash.PATCH_SITES:
            self.load(site.address, site.expected)

    def load(self, address, raw):
        self.memory.update((address + n, b) for n, b in enumerate(raw))

    def read_mem(self, address, size):
        return bytes(self.memory.get(address + n, 0) for n in range(size))

    def write_mem(self, address, raw):
        self.writes.append((address, raw))
        self.load(address, raw)


def execute(raw, address, registers, memory):
    """Evaluate the actual decoded integer instructions, not a parallel hook model."""
    md = Cs(CS_ARCH_ARM, CS_MODE_ARM)
    md.detail = True
    zero = False
    for ins in md.disasm(raw, address):
        name = ins.mnemonic
        if name.endswith('ne'):
            if zero:
                continue
            name = name[:-2]
        ops = ins.operands

        def value(op):
            if op.type == ARM_OP_IMM:
                return op.imm
            if op.type == ARM_OP_REG:
                return registers[ins.reg_name(op.reg)]
            raise AssertionError('unsupported operand')

        if name == 'tst':
            zero = not (value(ops[0]) & value(ops[1]))
            continue
        dest = ins.reg_name(ops[0].reg)
        if name in ('ldr', 'str'):
            mem = ops[1].mem
            location = registers[ins.reg_name(mem.base)] + mem.disp
            if name == 'ldr':
                registers[dest] = memory[location]
            else:
                memory[location] = registers[dest]
            continue
        a = value(ops[1])
        b = ops[1].shift.value if name == 'lsr' and len(ops) == 2 else value(ops[2])
        functions = {'bic': lambda: a & ~b, 'orr': lambda: a | b,
                     'and': lambda: a & b, 'eor': lambda: a ^ b,
                     'lsr': lambda: a >> b}
        if name not in functions:
            raise AssertionError(f'unsupported instruction {name}')
        registers[dest] = functions[name]() & 0xFFFFFFFF


class ButtonDashTests(unittest.TestCase):
    def test_patch_is_inline_with_no_caves_calls_or_mouse_abi_change(self):
        md = Cs(CS_ARCH_ARM, CS_MODE_ARM)
        instructions = list(md.disasm(dash.PATCH_SITES[0].replacement, dash.PATCH_SITES[0].address))
        self.assertEqual([(i.mnemonic, i.op_str) for i in instructions], [
            ('ldr', 'r0, [r7]'), ('tst', 'r0, #0x1000'),
            ('bic', 'r5, r5, #0x800'), ('orrne', 'r5, r5, #0x800')])
        self.assertEqual([len(s.replacement) for s in dash.PATCH_SITES], [16, 12])

    def test_flick_and_other_mapped_dash_sources_cannot_trigger_without_button(self):
        for hid in (0, 0x200, 0xF0000000, 0xFFFFFFFF & ~dash.HID_BUTTON):
            registers = {'r0': 0x800, 'r5': 0x2A81E, 'r7': 0x100}
            memory = {0x100: hid, 0x104: 0x3F800000, 0x108: 0x3F000000}
            before = dict(memory)
            execute(dash.PATCH_SITES[0].replacement, 0x0060FEEC, registers, memory)
            self.assertEqual(registers['r5'], 0x2A81E & ~dash.DASH_ACTION)
            self.assertEqual(memory, before)

    def test_press_hold_release_and_repress_use_original_game_edges(self):
        edge_bytes = dict((a, raw) for a, raw, _ in dash.GUARDS)[0x0060FFA8]
        memory = {0x100: 0, 0x218: 0x2001E}
        events = []
        for held in (False, True, True, True, False, True):
            memory[0x100] = dash.HID_BUTTON if held else 0
            registers = {'r0': 0, 'r1': 0, 'r2': 0, 'r4': 0x200, 'r5': 0x2001E, 'r7': 0x100}
            execute(dash.PATCH_SITES[0].replacement, 0x0060FEEC, registers, memory)
            execute(edge_bytes, 0x0060FFA8, registers, memory)
            events.append(bool(memory[0x21C] & dash.DASH_ACTION))
            memory[0x218] = registers['r5']
            self.assertEqual(registers['r5'] & ~dash.DASH_ACTION, 0x2001E)
        self.assertEqual(events, [False, True, False, False, False, True])

    def test_both_ground_modes_take_the_same_pressed_request(self):
        mode1 = dict((a, raw) for a, raw, _ in dash.GUARDS)[0x006104A8][:12]
        for held in (0, 0x800, 0x1000000):
            for pressed in (0, 0x800, 0x1000000):
                memory = {0x104: held, 0x108: pressed}
                regs0 = {'r1': 0x100}
                execute(dash.PATCH_SITES[1].replacement, 0x006104DC, regs0, memory)
                regs1 = {'r1': 0x100, 'r2': 0}
                execute(mode1, 0x006104A8, regs1, memory)
                self.assertEqual(regs0['r1'], int(bool(pressed & 0x800)))
                self.assertEqual(regs0['r1'], regs1['r2'])

    def test_round_trip_and_records(self):
        guest = Guest()
        before = dict(guest.memory)
        self.assertEqual(dash.status(guest)['state'], 'stock')
        result = dash.apply(guest)
        self.assertEqual(result['state'], 'installed')
        self.assertEqual(len(guest.writes), 2)
        for record in result['sites']:
            self.assertEqual(record['build_sha256'], dash.CODE_SHA256)
            self.assertTrue(record['original_hex'])
            self.assertTrue(record['replacement_hex'])
        self.assertTrue(dash.revert(guest)['restored'])
        self.assertEqual(guest.memory, before)

    def test_each_guard_and_mixed_or_foreign_patch_refuses_without_writes(self):
        targets = [(a, raw) for a, raw, _ in dash.GUARDS]
        targets += [(s.address, s.expected) for s in dash.PATCH_SITES]
        for address, raw in targets:
            guest = Guest()
            guest.load(address, bytes(b ^ 0xFF for b in raw))
            self.assertEqual(dash.status(guest)['state'], 'unknown')
            for operation in (dash.apply, dash.revert):
                with self.assertRaises(RuntimeError):
                    operation(guest)
            self.assertEqual(guest.writes, [])
        guest = Guest()
        guest.load(dash.PATCH_SITES[0].address, dash.PATCH_SITES[0].replacement)
        with self.assertRaises(RuntimeError):
            dash.apply(guest)
        self.assertEqual(guest.writes, [])

    def test_failed_second_write_rolls_back_apply_and_revert(self):
        for restore in (False, True):
            guest = Guest()
            if restore:
                dash.apply(guest)
            before = dict(guest.memory)
            original = guest.write_mem

            def fail(address, raw):
                original(address, raw)
                if address == dash.PATCH_SITES[1].address:
                    raise OSError('lost write reply')

            with mock.patch.object(guest, 'write_mem', side_effect=fail):
                with self.assertRaises(OSError):
                    (dash.revert if restore else dash.apply)(guest)
            self.assertEqual(guest.memory, before)

    def test_optional_private_image_fingerprints(self):
        path = ROOT / 'local/extracted/exefs_code.bin'
        if not path.exists():
            self.skipTest('optional user-owned code image absent')
        image = path.read_bytes()
        self.assertEqual(hashlib.sha256(image).hexdigest(), dash.CODE_SHA256)
        for address, raw in ([(a, raw) for a, raw, _ in dash.GUARDS] +
                             [(s.address, s.expected) for s in dash.PATCH_SITES]):
            self.assertEqual(image[address - 0x100000:address - 0x100000 + len(raw)], raw, hex(address))


if __name__ == '__main__':
    unittest.main()
