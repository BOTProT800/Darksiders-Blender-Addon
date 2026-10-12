"""Native ANM v1 curves, independently decoded from Deathinitive PC assets.

Coordinates are native local TRS; quaternions use (x, y, z, w). Times are
absolute source frames after accumulating unsigned byte deltas. This module
does not depend on Blender, change rest poses, or write game files.
"""
from __future__ import annotations

import math
import struct
from dataclasses import dataclass
from pathlib import Path

import numpy as np


class AnmParseError(ValueError):
    """Corrupt or unsupported animation; do not create a partial action."""


@dataclass(frozen=True, slots=True)
class Curve:
    frames: np.ndarray
    values: np.ndarray
    quaternion: bool = False

    def sample(self, frame: float) -> np.ndarray:
        if not math.isfinite(frame):
            raise ValueError("tiempo no finito")
        right = int(np.searchsorted(self.frames, frame, side="right"))
        if right == 0:
            return self.values[0].copy()
        if right == len(self.frames):
            return self.values[-1].copy()
        left = right - 1
        t = (frame - self.frames[left]) / (self.frames[right] - self.frames[left])
        a, b = self.values[left], self.values[right]
        if not self.quaternion:
            return a * (1 - t) + b * t
        dot = float(np.dot(a, b))
        if dot < 0:
            b, dot = -b, -dot
        if dot > 0.9999:
            q = a * (1 - t) + b * t
        else:
            angle = math.acos(min(1.0, dot))
            q = (a * math.sin((1 - t) * angle) + b * math.sin(t * angle)) / math.sin(angle)
        return q / np.linalg.norm(q)


@dataclass(frozen=True, slots=True)
class Track:
    name: str | None
    name_hash: int | None
    flags: int
    weight: int
    rotation: Curve | None
    position: Curve | None
    scale: Curve | None
    scalar: Curve | None
    offset: int
    end: int

    @property
    def identifier(self) -> str:
        return self.name if self.name is not None else f"0x{self.name_hash:016x}"


@dataclass(frozen=True, slots=True)
class Animation:
    name: str
    fps: int
    frame_count: int
    flags: int
    tracks: tuple[Track, ...]
    path: Path | None = None

    @property
    def duration(self) -> float:
        return (self.frame_count - 1) / self.fps

    @property
    def loop(self) -> bool:
        return bool(self.flags & 1)


class _Reader:
    def __init__(self, data: bytes):
        self.data, self.at = data, 16

    def take(self, size: int) -> bytes:
        end = self.at + size
        if size < 0 or end > len(self.data):
            raise AnmParseError(f"ANM truncado @{self.at:#x}: faltan {end - len(self.data)} bytes")
        value = self.data[self.at:end]
        self.at = end
        return value

    def unpack(self, fmt: str):
        return struct.unpack("<" + fmt, self.take(struct.calcsize("<" + fmt)))

    def align(self, size: int):
        self.take((-self.at) % size)

    def string(self) -> str:
        end = self.data.find(b"\0", self.at)
        if end < 0:
            raise AnmParseError(f"cadena sin terminador @{self.at:#x}")
        return self.take(end - self.at + 1)[:-1].decode("latin1")

    def curve(self, kind: str, flags: int) -> Curve | None:
        count, = self.unpack("H")
        if not count:
            return None
        segments, used = [], 0
        if kind in ("rotation", "position"):
            while used < count:
                if kind == "rotation":
                    segment = self.unpack("4B")
                    run = (segment[3] & 63) + 1
                else:
                    segment = self.unpack("Hhhh")
                    run = segment[0]
                if not run or used + run > count:
                    raise AnmParseError(f"segmento {kind} inválido @{self.at:#x}")
                segments.append((used, run, segment))
                used += run
            residuals = np.frombuffer(self.take(count * 3), dtype=np.int8).reshape(count, 3)
        elif kind == "scale":
            values = np.frombuffer(self.take(count * 6), dtype="<f2").reshape(count, 3).astype(float)
        else:
            values = np.frombuffer(self.take(count), dtype=np.uint8).astype(float)[:, None] / 255.0
        frames = np.cumsum(np.frombuffer(self.take(count), dtype=np.uint8), dtype=np.int64)
        if count > 1 and np.any(np.diff(frames) <= 0):
            raise AnmParseError(f"tiempos {kind} repetidos @{self.at:#x}")
        self.align(2)
        if kind == "position":
            values = np.empty((count, 3))
            for start, run, segment in segments:
                values[start:start + run] = (
                    np.asarray(segment[1:], dtype=np.float32) * np.float32(0.5)
                    + residuals[start:start + run].astype(np.float32) * np.float32(0.032)
                ) * (0.03125 if flags & 16 else 1.0)
        elif kind == "rotation":
            values = np.empty((count, 4))
            for seg_index, (start, run, segment) in enumerate(segments):
                base = np.asarray([((v & 63) ^ 32) - 32 for v in segment[:3]])
                steps = np.asarray([(1, 2, 4, 16)[v >> 6] for v in segment[:3]])
                following = segments[min(seg_index + 1, len(segments) - 1)][2]
                next_base = np.asarray([((v & 63) ^ 32) - 32 for v in following[:3]])
                end_time = frames[min(start + run, count - 1)]
                span = max(1, int(end_time - frames[start]))
                fraction = (frames[start:start + run] - frames[start]).astype(np.float32) * np.float32(256) / np.float32(span)
                interpolated = np.trunc(fraction[:, None] * (next_base - base).astype(np.float32)).astype(np.int64)
                integers = base * 256 + interpolated + residuals[start:start + run] * steps + (128 if flags & 32 else 0)
                small = integers.astype(np.int16).astype(np.float32) * np.float32(8.632728713564575e-05)
                squared = (small * small).sum(axis=1)
                if np.any(squared > 1.0001):
                    raise AnmParseError(f"cuaternión comprimido inválido @{self.at:#x}")
                missing = np.sqrt(np.maximum(np.float32(0), np.float32(1) - squared))
                omitted = segment[3] >> 6
                values[start:start + run] = np.insert(small, omitted, missing, axis=1)
            values /= np.linalg.norm(values, axis=1)[:, None]
        if not np.isfinite(values).all():
            raise AnmParseError(f"valores {kind} no finitos @{self.at:#x}")
        return Curve(frames, values, kind == "rotation")


def parse_anm(data: bytes, path: Path | None = None) -> Animation:
    if len(data) < 17 or data[:3] != b"ANM":
        raise AnmParseError("no empieza por ANM o cabecera truncada")
    if data[3] != 1 or data[4] != 0:
        raise AnmParseError(f"ANM versión/endianness sin soporte: {data[3:5].hex()}")
    fps, count, size, flags = struct.unpack_from("<BHII", data, 5)
    if size != len(data) - 16 or not fps or not count or flags & ~3:
        raise AnmParseError("tamaño, frecuencia, duración o flags ANM inválidos")
    reader = _Reader(data)
    name, tracks = reader.string(), []
    while reader.at < len(data):
        offset = reader.at
        track_flags, = reader.unpack("B")
        if track_flags & ~63:
            raise AnmParseError(f"flags de pista sin soporte @{offset:#x}: {track_flags:#x}")
        track_name, name_hash = None, None
        if track_flags & 1:
            track_name = reader.string()
        else:
            reader.align(8)
            name_hash, = reader.unpack("Q")
        reader.align(2)
        weight, = reader.unpack("h")
        rotation = reader.curve("rotation", track_flags)
        position = reader.curve("position", track_flags) if track_flags & 2 else None
        scale = reader.curve("scale", track_flags) if track_flags & 4 else None
        scalar = reader.curve("scalar", track_flags) if track_flags & 8 else None
        tracks.append(Track(track_name, name_hash, track_flags, weight, rotation, position, scale, scalar, offset, reader.at))
    if not tracks:
        raise AnmParseError("ANM sin pistas")
    return Animation(name, fps, count, flags, tuple(tracks), path)


def load_anm(path: Path) -> Animation:
    path = Path(path)
    return parse_anm(path.read_bytes(), path)


def bind_tracks(animation: Animation, skeleton) -> tuple[dict[int, Track], tuple[str, ...]]:
    """Exact names/serialized hashes only. Report every unmatched track."""
    hashes = {}
    names = {}
    for index, bone in enumerate(skeleton.bones):
        if bone.name in names or bone.name_hash is not None and bone.name_hash in hashes:
            raise ValueError("nombres o hashes de hueso ambiguos")
        names[bone.name] = index
        if bone.name_hash is not None:
            hashes[bone.name_hash] = index
    bound, missing = {}, []
    for track in animation.tracks:
        index = names.get(track.name) if track.name is not None else hashes.get(track.name_hash)
        if index is None:
            missing.append(track.identifier)
        elif index in bound:
            raise ValueError(f"dos pistas animan el mismo hueso: {skeleton.bones[index].name}")
        else:
            bound[index] = track
    return bound, tuple(missing)


def quaternion_matrix(q) -> np.ndarray:
    """Native passive XYZW quaternion -> Blender column-vector rotation.

    ANM uses the conjugate of Blender's active quaternion. Confirmed against
    O3D rest rotations (jaw/toes) and animated mesh silhouettes. Merely decoding
    the four components accurately does not validate this convention.
    """
    x, y, z, w = q
    return np.array([[1 - 2*(y*y+z*z), 2*(x*y-z*w), 2*(x*z+y*w)],
                     [2*(x*y+z*w), 1 - 2*(x*x+z*z), 2*(y*z-x*w)],
                     [2*(x*z-y*w), 2*(y*z+x*w), 1 - 2*(x*x+y*y)]]).T


def sample_local(skeleton, bound: dict[int, Track], frame: float) -> np.ndarray:
    """Column matrices; absent channels keep source rest values."""
    matrices = skeleton.local_matrices().transpose(0, 2, 1).copy()
    for index, track in bound.items():
        matrix = matrices[index]
        if track.position is not None:
            matrix[:3, 3] = track.position.sample(frame)
        scale = (track.scale.sample(frame) if track.scale is not None
                 else np.asarray(skeleton.bones[index].scale))
        if track.rotation is not None:
            matrix[:3, :3] = quaternion_matrix(track.rotation.sample(frame)) @ np.diag(scale)
        elif track.scale is not None:
            matrix[:3, :3] = matrix[:3, :3] @ np.diag(scale / np.asarray(skeleton.bones[index].scale))
    return matrices


def sample_world(skeleton, bound: dict[int, Track], frame: float, *, face_forward=False) -> np.ndarray:
    matrices = sample_local(skeleton, bound, frame)
    orientation = np.diag((-1., -1., 1., 1.)) if face_forward else np.eye(4)
    for index, bone in enumerate(skeleton.bones):
        matrices[index] = ((matrices[bone.parent] if bone.parent >= 0 else orientation)
                           @ matrices[index])
    return matrices
