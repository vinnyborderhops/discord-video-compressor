from compressor.config import ConfigurationStore
from compressor.encoders import (
    ENCODER_OPTIONS,
    detect_preferred_encoder,
    get_encoder_options,
    select_encoder,
)


def test_encoder_options_preserve_profile_and_add_rate_control():
    options = get_encoder_options("nvidia", 1000.4)
    assert options["preset"] == "p7"
    assert options["multipass"] == "fullres"
    assert options["b:v"] == "1000k"
    assert options["maxrate"] == "1500k"
    assert options["bufsize"] == "2000k"
    options["preset"] = "changed"
    assert ENCODER_OPTIONS["nvidia"]["preset"] == "p7"


def test_videotoolbox_does_not_receive_vbv_options():
    options = get_encoder_options("mac", 500)
    assert options["b:v"] == "500k"
    assert "maxrate" not in options
    assert "bufsize" not in options


def test_detection_uses_priority_among_all_working_encoders():
    checked = []

    def checker(encoder_type, _tools):
        checked.append(encoder_type)
        return encoder_type in {"amd", "cpu"}

    selection = detect_preferred_encoder(
        object(),
        availability_checker=checker,
    )
    assert selection.encoder_type == "amd"
    assert checked == ["nvidia", "amd", "intel", "mac", "cpu"]


def test_valid_cache_checks_exactly_one_encoder(tmp_path):
    store = ConfigurationStore(tmp_path)
    store.save_encoder_cache("intel", "h264_qsv")
    checked = []

    def checker(encoder_type, _tools):
        checked.append(encoder_type)
        return True

    selection = select_encoder(
        object(),
        store,
        availability_checker=checker,
    )
    assert selection.source == "cache"
    assert selection.encoder_type == "intel"
    assert checked == ["intel"]


def test_stale_cache_triggers_full_detection_and_rewrite(tmp_path):
    store = ConfigurationStore(tmp_path)
    store.save_encoder_cache("nvidia", "h264_nvenc")
    checked = []

    def checker(encoder_type, _tools):
        checked.append(encoder_type)
        return encoder_type == "cpu"

    selection = select_encoder(
        object(),
        store,
        availability_checker=checker,
    )
    assert selection.encoder_type == "cpu"
    assert checked[0] == "nvidia"
    assert checked[1:] == ["nvidia", "amd", "intel", "mac", "cpu"]
    record = store.load_encoder_cache({"cpu": "libx264"})
    assert record is not None


def test_manual_override_does_not_modify_cache(tmp_path):
    store = ConfigurationStore(tmp_path)
    store.save_encoder_cache("nvidia", "h264_nvenc")
    selection = select_encoder(
        object(),
        store,
        requested_encoder="cpu",
        availability_checker=lambda encoder_type, _tools: encoder_type == "cpu",
    )
    assert selection.source == "manual"
    record = store.load_encoder_cache({"nvidia": "h264_nvenc"})
    assert record is not None
