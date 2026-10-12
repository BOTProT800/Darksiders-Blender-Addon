"""Known quantized samples, interpolation and hostile ANM bounds (no assets)."""
import math
import struct

import numpy as np
import pytest

from io_darksiders.formats.animation import (
    AnmParseError, Curve, bind_tracks, parse_anm, sample_world, quaternion_matrix,
)
from io_darksiders.formats.skeleton import Bone, Skeleton, parse_o3d
from tests.test_skeleton import BodWriter, build_o3d


def fixture(*, flags=0x2E, inline=False):
    # Identity rotation, translation two segments (x=1 -> 3), half scale and
    # scalar endpoints. Deliberately begins the hashed track off alignment.
    data = bytearray(b"ANM\x01\x00\x1e" + struct.pack("<HII", 11, 0, 1) + b"Test\0")
    data.append(flags | int(inline))
    if inline:
        data.extend(b'root\0')
    else:
        data.extend(bytes((-len(data)) % 8))
        data.extend(struct.pack('<Q', 0x123456789abcdef0))
    data.extend(bytes((-len(data)) % 2))
    data.extend(struct.pack('<hH', 32767, 1))
    data.extend(bytes([63, 63, 63, 192, 127, 127, 127, 0]))
    if flags & 2:
        data.extend(struct.pack('<HHhhhHhhh', 2, 1, 2, 0, 0, 1, 6, 0, 0))
        data.extend(bytes([0, 0, 0, 0, 0, 0, 0, 10]))
    if flags & 4:
        data.extend(struct.pack('<H6e2B', 2, 1, 1, 1, 2, 3, 4, 0, 10))
    if flags & 8:
        data.extend(struct.pack('<H4B', 2, 0, 255, 0, 10))
    struct.pack_into('<I', data, 8, len(data) - 16)
    return bytes(data)


def rig():
    return Skeleton('rig', (Bone('root', -1, (0, 0, 0), (0, 0, 0), name_hash=0x123456789abcdef0),
                            Bone('child', 0, (0, 2, 0), (0, 0, 0), name_hash=2)))


def test_known_channels_and_time():
    clip = parse_anm(fixture())
    assert clip.name == 'Test' and clip.loop and clip.duration == 1 / 3
    track, = clip.tracks
    np.testing.assert_allclose(track.position.sample(5), (2, 0, 0))
    np.testing.assert_allclose(track.scale.sample(5), (1.5, 2, 2.5))
    np.testing.assert_allclose(track.scalar.sample(5), (0.5,))
    np.testing.assert_allclose(track.rotation.values[0], (-8.6327286e-5,) * 3 + (0.9999999888,), atol=1e-10)
    assert track.end == len(fixture())


@pytest.mark.parametrize('omitted', range(4))
def test_rotation_segments_irregular_times_and_all_residual_steps(omitted):
    raw = bytearray(fixture(flags=0x20)[:34])
    raw.extend(struct.pack('<H', 3))
    raw.extend(bytes([127, 128, 254, (omitted << 6) | 1,
                      1, 0, 62, omitted << 6]))
    raw.extend(struct.pack('<9b', 4, 2, -1, 0, 0, 0, 0, 0, 0))
    raw.extend(bytes([0, 1, 9]))
    struct.pack_into('<I', raw, 8, len(raw)-16)
    curve = parse_anm(raw).tracks[0].rotation
    # At t=1, base-X interpolates from -1 to +1 over ten frames:
    # -256 + trunc(2 * 256 / 10) + 128 = -77 (not -76 or a key-index lerp).
    expected = np.array([[-120,136,-400],[-77,128,-384],[384,128,-384]]) * 8.632728713564575e-05
    np.testing.assert_allclose(np.delete(curve.values, omitted, axis=1), expected, atol=2e-8)
    np.testing.assert_allclose(np.linalg.norm(curve.values, axis=1), 1, atol=1e-12)
    raw[-1] = 0
    with pytest.raises(AnmParseError, match='tiempos'):
        parse_anm(raw)


def test_duplicate_tracks_are_preserved_but_ambiguous_binding_rejected():
    raw = bytearray(fixture(inline=True))
    track = raw[21:]
    # First track starts odd and pads after flag + 'root\0'. The next starts
    # even, so its six-byte prefix needs no alignment byte before the weight.
    raw.extend(track[:6] + track[7:])
    struct.pack_into('<I', raw, 8, len(raw)-16)
    clip = parse_anm(raw)
    assert len(clip.tracks) == 2
    with pytest.raises(ValueError, match='dos pistas'):
        bind_tracks(clip, rig())


def test_fine_translation_and_inline_name():
    clip = parse_anm(fixture(flags=0x32, inline=True))
    bound, missing = bind_tracks(clip, rig())
    assert list(bound) == [0] and not missing
    np.testing.assert_allclose(bound[0].position.sample(10), (3 / 32, 0, 0))


def test_slerp_shortest_path_and_clamping():
    curve = Curve(np.array([0, 10]), np.array([[0, 0, 0, 1], [0, 0, -1, 0.]]), True)
    np.testing.assert_allclose(curve.sample(5), (0, 0, -math.sqrt(.5), math.sqrt(.5)))
    np.testing.assert_equal(curve.sample(-1), curve.values[0])
    np.testing.assert_equal(curve.sample(11), curve.values[-1])
    with pytest.raises(ValueError):
        curve.sample(float('nan'))


def test_native_quaternion_uses_passive_rotation():
    # Native negative Z quarter turn is Blender positive Z quarter turn.
    matrix = quaternion_matrix((0, 0, -math.sqrt(.5), math.sqrt(.5)))
    np.testing.assert_allclose(matrix @ (1, 0, 0), (0, 1, 0), atol=1e-15)


def test_bind_by_hash_never_track_order():
    skeleton = rig()
    bound, missing = bind_tracks(parse_anm(fixture()), skeleton)
    assert set(bound) == {0} and missing == ()
    skeleton.bones = tuple(reversed(skeleton.bones))
    bound, missing = bind_tracks(parse_anm(fixture()), skeleton)
    assert set(bound) == {1}
    other = Skeleton('other', (Bone('other', -1, (0, 0, 0), (0, 0, 0), name_hash=0),))
    bound, missing = bind_tracks(parse_anm(fixture()), other)
    assert not bound and missing == ('0x123456789abcdef0',)


def test_hash_collision_rejected():
    skeleton = Skeleton('bad', (Bone('a', -1, (0, 0, 0), (0, 0, 0), name_hash=1),
                                Bone('b', 0, (0, 1, 0), (0, 0, 0), name_hash=1)))
    with pytest.raises(ValueError, match='ambiguos'):
        bind_tracks(parse_anm(fixture()), skeleton)


def test_world_and_orientation_include_unanimated_descendants():
    skeleton = rig()
    bound, _ = bind_tracks(parse_anm(fixture(flags=0x22)), skeleton)
    native = sample_world(skeleton, bound, 5)
    forward = sample_world(skeleton, bound, 5, face_forward=True)
    np.testing.assert_allclose(forward, np.diag([-1, -1, 1, 1]) @ native)
    np.testing.assert_allclose(native[1, :3, 3], (2, 2, 0), atol=.001)


@pytest.mark.parametrize('length', range(len(fixture())))
def test_every_truncation_is_rejected(length):
    raw = bytearray(fixture()[:length])
    if length >= 16:
        struct.pack_into('<I', raw, 8, length - 16)
    with pytest.raises(AnmParseError):
        parse_anm(bytes(raw))


@pytest.mark.parametrize('offset,value', [(3,2),(4,1),(5,0),(6,0),(12,4),(21,128)])
def test_unsupported_header_and_flags(offset, value):
    raw = bytearray(fixture())
    raw[offset] = value
    with pytest.raises(AnmParseError):
        parse_anm(bytes(raw))


def test_zero_run_and_nonfinite_scale_rejected():
    raw = bytearray(fixture())
    # rotation ends at 44; translation count then run.
    struct.pack_into('<H', raw, 46, 0)
    with pytest.raises(AnmParseError):
        parse_anm(raw)
    raw = bytearray(fixture())
    struct.pack_into('<H', raw, 72, 0x7c00)
    with pytest.raises(AnmParseError, match='no finitos'):
        parse_anm(raw)


def test_o3d_retains_serialized_name_hash(monkeypatch):
    original = BodWriter.string
    def string(self, text):
        at = len(self.out)
        fresh = text not in self.ids
        result = original(self, text)
        if fresh and text == 'root':
            struct.pack_into('<Q', self.out, at + 1, 0x123456789abcdef0)
        return result
    monkeypatch.setattr(BodWriter, 'string', string)
    skeleton = parse_o3d(build_o3d([{'name':'root'}]))
    assert skeleton.bones[0].name_hash == 0x123456789abcdef0
    assert bind_tracks(parse_anm(fixture()), skeleton)[1] == ()
