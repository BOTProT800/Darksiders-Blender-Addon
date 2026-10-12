"""Optional independent ANM oracle: emulate local executable math, not the game.

Requires pefile 2024.8.26 + Unicorn 2.1.4 (QA only, never shipped with addon).
No game bytes are embedded or written by this script. The executable hash is
checked before using build-specific RVAs; math-library calls are isolated shims.
"""
import argparse
import hashlib
import json
import math
from pathlib import Path
import struct
import sys

import numpy as np

EXPECTED_EXE = '5580738ef70bc5bbcc72d7dc4a9c319956cd14dbfef6f9dbec54c1b5d97799fb'


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--exe', type=Path, required=True)
    parser.add_argument('--assets-root', type=Path, required=True)
    parser.add_argument('--tools-dir', type=Path)
    parser.add_argument('--output', type=Path, required=True)
    parser.add_argument('clips', nargs='+')
    args = parser.parse_args()
    if args.output.exists():
        parser.error('el informe de destino ya existe')
    raw = args.exe.read_bytes()
    if hashlib.sha256(raw).hexdigest() != EXPECTED_EXE:
        parser.error('ejecutable distinto del build validado; no se pueden reutilizar sus RVAs')
    if args.tools_dir:
        sys.path.insert(0, str(args.tools_dir.resolve()))
    sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
    import pefile
    from unicorn import Uc, UC_ARCH_X86, UC_MODE_64, UC_HOOK_CODE
    from unicorn import x86_const as regs
    from io_darksiders.formats.animation import load_anm
    pe = pefile.PE(data=raw, fast_load=True)
    base = pe.OPTIONAL_HEADER.ImageBase
    uc = Uc(UC_ARCH_X86, UC_MODE_64)
    uc.mem_map(base, (pe.OPTIONAL_HEADER.SizeOfImage + 4095) & ~4095)
    uc.mem_write(base, pe.get_memory_mapped_image())
    mem = 0x10000000
    uc.mem_map(mem, 0x800000)
    stack, stop = mem + 0x7ff008, mem + 0x7fff00
    obj, out = mem + 0x600000, mem + 0x600100
    math_functions = {0x11067e6: math.sqrt, 0x1088f70: math.acos, 0xd7f128: math.sin}

    def hook(cpu, address, size, data):
        rva = address - base
        if rva in math_functions:
            value = struct.unpack('<f', struct.pack('<I', cpu.reg_read(regs.UC_X86_REG_XMM0) & 0xffffffff))[0]
            result = math_functions[rva](value)
            cpu.reg_write(regs.UC_X86_REG_XMM0, struct.unpack('<I', struct.pack('<f', result))[0])
        sp = cpu.reg_read(regs.UC_X86_REG_RSP)
        cpu.reg_write(regs.UC_X86_REG_RIP, struct.unpack('<Q', cpu.mem_read(sp, 8))[0])
        cpu.reg_write(regs.UC_X86_REG_RSP, sp + 8)

    for rva in [*math_functions, 0xd7eea0]:  # stack-cookie check has no math effect
        uc.hook_add(UC_HOOK_CODE, hook, begin=base+rva, end=base+rva)

    def call(rva, rcx, rdx=0, r8=0, frame=None, quat=False):
        uc.reg_write(regs.UC_X86_REG_RSP, stack)
        uc.mem_write(stack, struct.pack('<Q', stop))
        for register, value in [(regs.UC_X86_REG_RCX, rcx), (regs.UC_X86_REG_RDX, rdx), (regs.UC_X86_REG_R8, r8)]:
            uc.reg_write(register, value)
        if frame is not None:
            uc.reg_write(regs.UC_X86_REG_XMM2 if quat else regs.UC_X86_REG_XMM1,
                         struct.unpack('<I', struct.pack('<f', frame))[0])
        uc.emu_start(base+rva, stop, count=2_000_000)
        if uc.reg_read(regs.UC_X86_REG_RIP) != stop:
            raise RuntimeError('el evaluador no retornó dentro del límite de instrucciones')

    results = []
    limits = {'rotation':3e-6, 'position':.002, 'scale':1e-5, 'scalar':1e-6}
    for relative in args.clips:
        path = args.assets_root / relative
        data = path.read_bytes()
        if len(data) + 32 >= 0x600000:
            raise ValueError('clip mayor que el buffer aislado del oráculo')
        clip = load_anm(path)
        uc.mem_write(mem, data + bytes(32))
        errors, worst, samples = {k:0. for k in limits}, {}, 0
        for track in clip.tracks:
            call(0x7c6a68, obj, mem+track.offset)
            for kind, rva, width in [('rotation',0x7ea584,4), ('position',0x7e9bcc,3),
                                      ('scale',0x7e9ea4,3), ('scalar',0x7ea3d0,1)]:
                curve = getattr(track, kind)
                if curve is None:
                    continue
                times = sorted(set([float(t) for t in curve.frames] +
                                   [(float(a)+float(b))*.5 for a,b in zip(curve.frames[:-1],curve.frames[1:])]))
                for frame in times:
                    if kind == 'rotation':
                        call(rva, obj, out, frame=frame, quat=True)
                    else:
                        call(rva, obj, r8=out, frame=frame)
                    actual = np.array(struct.unpack('<'+str(width)+'f', uc.mem_read(out,width*4)))
                    expected = curve.sample(frame)
                    error = float(np.max(abs(actual-expected)))
                    if kind == 'rotation':
                        error = min(error, float(np.max(abs(actual+expected))))
                    if error > errors[kind]:
                        errors[kind] = error
                        worst[kind] = {'track':track.identifier,'frame':frame}
                    samples += 1
        result = {'file':relative,'samples':samples,'max_component_errors':errors,'worst':worst,
                  'passed':all(errors[k] <= limits[k] for k in limits)}
        results.append(result)
        print(json.dumps(result),flush=True)
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps({'exe_sha256':EXPECTED_EXE, 'tolerances':limits,
                                      'cases':results},indent=2),encoding='utf-8')
    if not all(r['passed'] for r in results):
        raise SystemExit('diferencias superiores a las tolerancias')


if __name__ == '__main__':
    main()
