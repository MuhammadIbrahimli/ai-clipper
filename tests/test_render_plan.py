import pytest

from clipper.render import plan


def words_at(times, text="word"):
    return [{"start": s, "end": s + 0.4, "word": text} for s in times]


def test_jump_cut_keeps_0_13s_of_silence_per_long_pause():
    # two groups separated by a 1.0 s pause
    words = words_at([0.0, 0.5]) + words_at([1.9, 2.4])
    ranges = plan.jump_cut_ranges(words, 0.0, 3.0)
    assert len(ranges) == 2
    kept_silence = (ranges[0][1] - words[1]["end"]) + (words[2]["start"] - ranges[1][0])  # tail + lead
    assert kept_silence == pytest.approx(2 * plan.PAD)


def test_short_pause_is_not_cut():
    words = words_at([0.0, 0.5, 1.0])  # gaps 0.1 s
    assert plan.jump_cut_ranges(words, 0.0, 2.0) == [(0.0, 1.0 + 0.4 + plan.PAD)]


def test_timeline_respects_max_output_with_teaser_first():
    words = words_at([i * 0.5 for i in range(200)])  # 100 s, no pauses
    tl = plan.build_timeline(words, 10.0, 90.0, 12.0, 15.0, max_out=55.0)
    assert tl.ranges[0] == (12.0, 15.0)  # teaser first
    assert tl.total == pytest.approx(55.0)
    assert sum(b - a for a, b in tl.ranges) == pytest.approx(tl.total)


def test_out_words_map_into_output_time():
    words = words_at([1.0, 2.0])
    tl = plan.Timeline([(1.0, 3.0)], 2.0)
    got = plan.out_words(words, tl)
    assert [(round(s, 3), round(e, 3), w) for s, e, w in got] == [(0.0, 0.4, "word"), (1.0, 1.4, "word")]


def test_zoom_accents_spacing_and_envelope():
    words = [(s, s + 0.4, "42" if int(s) % 5 == 0 else "w") for s in range(0, 60)]
    accents = plan.zoom_accents(words, 60.0)
    assert len(accents) >= 10
    gaps = [b - a for a, b in zip(accents, accents[1:])]
    assert all(4.0 <= g <= 6.0 for g in gaps)
    a = accents[0]
    assert plan.zoom_at(accents, a) == pytest.approx(1.12)
    assert plan.zoom_at(accents, a - 1.0) == 1.0
    assert plan.zoom_at(accents, a + 1.0) == 1.0


def test_callouts_only_on_digits_with_2_5s_duration():
    out = [(0.0, 0.4, "hello"), (5.0, 5.4, "90%"), (5.5, 5.9, "of"), (6.0, 6.4, "creators")]
    cos = plan.callouts(out, total=30.0)
    assert len(cos) == 1
    s, e, text = cos[0]
    assert e - s == pytest.approx(plan.CALLOUT_S) and "90%" in text


def test_ass_karaoke_and_format():
    out = [(0.0, 0.5, "one"), (0.5, 1.0, "two"), (1.0, 1.5, "three")]
    ass = plan.ass_subtitles(out, "karaoke", 1080, 1920)
    assert "PlayResX: 1080" in ass and "[Events]" in ass
    assert "{\\k50}one" in ass
    classic = plan.ass_subtitles(out, "classic", 1080, 1920)
    assert classic.count("Dialogue:") == 3  # one event per highlighted word
    assert plan._ass_time(61.5) == "0:01:01.50"
