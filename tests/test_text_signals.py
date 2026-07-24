"""Тесты для text_signals.py — разбор текста «о дне» по словам."""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from text_signals import (
    STOPWORDS,
    activity_stems,
    day_terms,
    is_covered,
    normalize,
    stem,
)


class TestStem:
    def test_name_forms_collapse(self):
        forms = ["Маша", "Маше", "Машу", "Машей", "Маши"]
        assert len({stem(form) for form in forms}) == 1

    def test_verb_forms_collapse(self):
        assert stem("подтягивался") == stem("подтягивалась")

    def test_yo_is_normalized(self):
        assert normalize("Ёлка") == "елка"

    def test_short_word_is_not_cut_to_nothing(self):
        assert len(stem("сон")) >= 3
        assert stem("дом") == "дом"


class TestIsCovered:
    def _known(self):
        return activity_stems({"Воздержание", "Подтягивания", "Растяжка", "Ужин"})

    def test_matches_different_word_forms(self):
        known = self._known()
        for word in ["воздерживался", "подтягивался", "растягивался", "ужинал"]:
            assert is_covered(stem(word), known), word

    def test_unrelated_words_not_covered(self):
        known = self._known()
        for word in ["велик", "играл", "маша", "работа"]:
            assert not is_covered(stem(word), known), word

    def test_empty_known_set(self):
        assert is_covered("велик", set()) is False


class TestDayTerms:
    def test_drops_stopwords_and_short_words(self):
        stems, _, _ = day_terms("Я пошел в магазин потому что надо")
        assert "магазин" in " ".join(stems)
        for junk in ("пошел", "надо", "потому"):
            assert stem(junk) not in stems

    def test_excludes_activity_words(self):
        known = activity_stems({"Подтягивания"})
        stems, _, _ = day_terms("Сегодня подтягивался и катался на велике", exclude=known)
        assert not any(s.startswith("подтягив") for s in stems)
        assert any(s.startswith("велик") for s in stems)

    def test_counts_word_once_per_day(self):
        stems, _, _ = day_terms("велик велик велик")
        assert len([s for s in stems if s.startswith("велик")]) == 1

    def test_detects_proper_names_mid_sentence(self):
        _, _, names = day_terms("Сегодня встретился с Машей в Питере")
        assert stem("Маша") in names
        assert stem("Питер") in names

    def test_sentence_start_is_not_a_name(self):
        _, _, names = day_terms("Работал весь день. Устал сильно.")
        assert stem("Работал") not in names

    def test_empty_text(self):
        stems, display, names = day_terms("")
        assert stems == set() and display == {} and names == set()

    def test_display_keeps_original_word(self):
        _, display, _ = day_terms("катался на велике, велик хороший")
        assert display[stem("велик")] in ("велик", "велике")


class TestStopwords:
    def test_common_junk_present(self):
        for word in ["пошел", "надо", "очень", "просто", "было"]:
            assert word in STOPWORDS
