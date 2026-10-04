import pytest

from botonomus import HumanConfig as TopLevelConfig
from botonomus.human import DE_QWERTZ, HumanConfig, HumanProfile


def test_presets_are_ordered_by_pace():
    default = HumanConfig.preset("default")
    careful = HumanConfig.preset("careful")
    fast = HumanConfig.preset("fast")
    assert default == HumanConfig() and TopLevelConfig is HumanConfig
    assert careful.move_speed < default.move_speed < fast.move_speed
    assert careful.key_delay > default.key_delay > fast.key_delay
    assert careful.think[0] > default.think[0] > fast.think[0]
    assert careful.idle_moves and not default.idle_moves and not fast.idle_moves
    assert fast.mistype_rate < default.mistype_rate


def test_preset_overrides_and_replace():
    config = HumanConfig.preset("careful", mistype_rate=0.0, layout="de")
    assert config.mistype_rate == 0.0 and config.keyboard_layout is DE_QWERTZ
    assert config.move_speed == HumanConfig.preset("careful").move_speed
    assert config.replace(move_speed=2.0).move_speed == 2.0


@pytest.mark.parametrize(
    "changes",
    [
        {"mistype_rate": 1.5},
        {"overshoot_probability": -0.1},
        {"move_speed": 0},
        {"think": (0.5, 0.1)},
        {"click_hold": (0.0, 0.1)},
        {"mistype_notice": -1},
        {"layout": "nope"},
    ],
)
def test_invalid_values_are_rejected(changes):
    with pytest.raises(ValueError):
        HumanConfig(**changes)


def test_unknown_preset():
    with pytest.raises(ValueError, match="Unknown preset"):
        HumanConfig.preset("sloppy")  # type: ignore[arg-type]


def test_human_profile_is_a_compatible_alias():
    profile = HumanProfile(move_speed=1.3, key_delay=0.09, think=(0.1, 0.2))
    assert isinstance(profile, HumanConfig)
    assert profile.key_jitter == HumanConfig().key_jitter
