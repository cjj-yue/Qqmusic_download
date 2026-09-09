"""Export one locally cached QQ Music FLAC, without account credentials or network calls.

The optional Windows client scan is restricted to readable private memory of
QQMusic.exe and to audio-key candidates around this file's media identifier.
Only a key that decrypts a valid FLAC STREAMINFO header is used. Keys, memory
contents and credentials are never printed or saved.
"""
import argparse
import base64
import ctypes as ct
from ctypes import wintypes as wt
import json
import re
import struct
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent / 'lib'))
from crypto import derive_key, decrypt_tencent_tea
from rc4 import RC4Cipher


def stream_info(data):
    if data[:8] != b'fLaC\x00\x00\x00\x22':
        return None
    if len(data) < 42:
        return None
    min_block, max_block = struct.unpack('>HH', data[8:12])
    packed = int.from_bytes(data[18:26], 'big')
    rate = packed >> 44
    channels = ((packed >> 41) & 7) + 1
    bits = ((packed >> 36) & 31) + 1
    samples = packed & ((1 << 36) - 1)
    if not (16 <= min_block <= max_block <= 65535 and rate in (22050, 32000, 44100, 48000, 88200, 96000, 176400, 192000) and channels <= 8 and bits in (16, 24, 32) and samples > 0):
        return None
    return dict(sample_rate=rate, channels=channels, bits=bits, samples=samples,
                duration_seconds=round(samples / rate, 6), pcm_md5=data[26:42].hex())


class MapCipher:
    def __init__(self, key):
        self.key = key

    def decrypt(self, data, offset):
        for index in range(len(data)):
            position = offset + index
            if position > 0x7fff:
                position %= 0x7fff
            key_index = (position * position + 71214) % len(self.key)
            shift = (key_index + 4) & 7
            value = self.key[key_index]
            data[index] ^= ((value << shift) | (value >> shift)) & 255


def first_bytes(key, data):
    if not key:
        return b''
    if len(key) <= 300:
        result = bytearray(data[:42])
        MapCipher(key).decrypt(result, 0)
        return bytes(result)
    h = 1
    for v in key:
        if v:
            nxt = (h * v) & 0xffffffff
            if nxt == 0 or nxt <= h:
                break
            h = nxt
    result = bytearray(data[:42])
    for i in range(len(result)):
        seed = key[i % len(key)]
        skip = int(float(h) / ((i + 1) * seed) * 100.0) % len(key) if seed else 0
        result[i] ^= key[skip]
        if i < 8 and result[i] != b'fLaC\x00\x00\x00\x22'[i]:
            return b''
    return result


def key_variants(candidate):
    if 300 < len(candidate) <= 2048:
        yield candidate
    try:
        raw = base64.b64decode(candidate, validate=True)
        prefix = b'QQMusic EncV2,Key:'
        if raw.startswith(prefix):
            stage1 = decrypt_tencent_tea(raw[len(prefix):], b'386ZJY!@#*$%^&)(')
            stage2 = decrypt_tencent_tea(stage1, b'**#!(#$%&^a1cZ,T') if stage1 else None
            if stage2:
                raw = base64.b64decode(stage2, validate=True)
        if len(raw) > 300:
            yield raw
        if len(raw) >= 24 and (len(raw) - 8) % 8 == 0:
            key = derive_key(raw)
            if key:
                yield key
    except (ValueError, IndexError, struct.error):
        pass


def scan_audio_key(pid, marker, encrypted, seconds):
    if sys.platform != 'win32':
        raise RuntimeError('The optional client scan requires Windows.')
    kernel = ct.WinDLL('kernel32', use_last_error=True)
    kernel.OpenProcess.argtypes = [wt.DWORD, wt.BOOL, wt.DWORD]
    kernel.OpenProcess.restype = wt.HANDLE
    kernel.CloseHandle.argtypes = [wt.HANDLE]
    kernel.QueryFullProcessImageNameW.argtypes = [wt.HANDLE, wt.DWORD, wt.LPWSTR, ct.POINTER(wt.DWORD)]
    kernel.ReadProcessMemory.argtypes = [wt.HANDLE, ct.c_void_p, ct.c_void_p, ct.c_size_t, ct.POINTER(ct.c_size_t)]
    kernel.ReadProcessMemory.restype = wt.BOOL

    class MBI(ct.Structure):
        _fields_ = [('BaseAddress', ct.c_void_p), ('AllocationBase', ct.c_void_p),
                    ('AllocationProtect', wt.DWORD), ('PartitionId', wt.WORD),
                    ('RegionSize', ct.c_size_t), ('State', wt.DWORD),
                    ('Protect', wt.DWORD), ('Type', wt.DWORD)]

    kernel.VirtualQueryEx.argtypes = [wt.HANDLE, ct.c_void_p, ct.POINTER(MBI), ct.c_size_t]
    kernel.VirtualQueryEx.restype = ct.c_size_t
    handle = kernel.OpenProcess(0x0400 | 0x0010, False, pid)
    if not handle:
        raise OSError(ct.get_last_error(), 'Cannot read the selected QQ Music process.')
    try:
        exe = ct.create_unicode_buffer(32768)
        size = wt.DWORD(len(exe))
        if not kernel.QueryFullProcessImageNameW(handle, 0, exe, ct.byref(size)) or Path(exe.value).name.lower() != 'qqmusic.exe':
            raise RuntimeError('Refusing to inspect a process other than QQMusic.exe.')
        deadline = time.monotonic() + seconds
        address, checked, hits, regions, read_bytes = 0, set(), 0, 0, 0
        lengths = {}
        patterns = [marker.encode(), marker.encode('utf-16-le')]
        candidate_pattern = re.compile(rb'[A-Za-z0-9+/=]{320,2048}')
        next_report = time.monotonic() + 15
        while time.monotonic() < deadline and read_bytes < 1536 * 1024 * 1024:
            info = MBI()
            if not kernel.VirtualQueryEx(handle, address, ct.byref(info), ct.sizeof(info)):
                break
            base, length = info.BaseAddress or 0, info.RegionSize
            address = base + length
            if length == 0 or address <= base:
                break
            if info.State != 0x1000 or info.Type != 0x20000 or info.Protect & 0x100 or (info.Protect & 0xff) not in (2, 4, 8, 0x20, 0x40, 0x80):
                continue
            regions += 1
            offset = 0
            while offset < length and time.monotonic() < deadline:
                n = min(4 * 1024 * 1024, length - offset)
                buf = ct.create_string_buffer(n)
                got = ct.c_size_t()
                kernel.ReadProcessMemory(handle, base + offset, buf, n, ct.byref(got))
                data = buf.raw[:got.value]
                read_bytes += got.value
                for pattern in patterns:
                    pos = data.find(pattern)
                    while pos >= 0:
                        hits += 1
                        nearby = data[max(0, pos - 32768):min(len(data), pos + 32768)]
                        for view in (nearby, nearby.replace(b'\x00', b'')):
                            for match in candidate_pattern.finditer(view):
                                candidate = match.group()
                                if candidate in checked:
                                    continue
                                checked.add(candidate)
                                lengths[len(candidate)] = lengths.get(len(candidate), 0) + 1
                                for key in key_variants(candidate):
                                    details = stream_info(first_bytes(key, encrypted))
                                    if details:
                                        print(json.dumps(dict(status='matching_audio_key_found', marker_hits=hits, candidates=len(checked), **details)), flush=True)
                                        return key
                        pos = data.find(pattern, pos + len(pattern))
                offset += max(1, n - 65536) if n == 4 * 1024 * 1024 else n
                if time.monotonic() >= next_report:
                    print(json.dumps(dict(status='scanning', regions=regions, marker_hits=hits, candidates=len(checked), scanned_mb=read_bytes // 1048576)), flush=True)
                    next_report = time.monotonic() + 15
        print(json.dumps(dict(status='audio_key_not_found', regions=regions, marker_hits=hits, candidates=len(checked), candidate_lengths=lengths, scanned_mb=read_bytes // 1048576)), flush=True)
        return None
    finally:
        kernel.CloseHandle(handle)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('input', type=Path)
    parser.add_argument('output', type=Path)
    parser.add_argument('--media-mid', required=True)
    parser.add_argument('--pid', type=int, help='Read only song-related audio-key candidates from QQMusic.exe; no account authentication.')
    parser.add_argument('--ekey-file', type=Path, help='Use a song audio key already available locally.')
    parser.add_argument('--seconds', type=int, default=45)
    parser.add_argument('--expected-duration', type=float, default=191)
    args = parser.parse_args()
    if args.output.exists():
        raise RuntimeError('Output already exists; refusing to overwrite it.')
    with args.input.open('rb') as file:
        header = file.read(128)
    key = None
    if args.ekey_file:
        for trial in key_variants(args.ekey_file.read_bytes().strip()):
            if stream_info(first_bytes(trial, header)):
                key = trial
                break
    if key is None and args.pid:
        key = scan_audio_key(args.pid, args.media_mid, header, args.seconds)
    if key is None:
        print(json.dumps(dict(status='needs_song_audio_key', exported=False)))
        return 2
    info = stream_info(first_bytes(key, header))
    if abs(info['duration_seconds'] - args.expected_duration) > 3:
        raise RuntimeError('The decoded duration does not match the requested song.')
    cipher = RC4Cipher(key) if len(key) > 300 else MapCipher(key)
    args.output.parent.mkdir(parents=True, exist_ok=True)
    offset = 0
    with args.input.open('rb') as source, args.output.open('xb') as target:
        while data := source.read(5120 * 128):
            block = bytearray(data)
            cipher.decrypt(block, offset)
            target.write(block)
            offset += len(block)
    print(json.dumps(dict(status='exported_pending_full_decode_validation', file=str(args.output.resolve()), bytes=offset, **info), ensure_ascii=False))
    return 0


if __name__ == '__main__':
    try:
        sys.exit(main())
    except (OSError, RuntimeError) as error:
        print(json.dumps(dict(status='error', message=str(error)), ensure_ascii=False))
        sys.exit(1)
