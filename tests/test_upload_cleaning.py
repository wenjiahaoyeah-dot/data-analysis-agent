import pandas as pd

from app.services.upload_service import _clean_frame


def test_column_normalization_does_not_create_duplicate_names():
    df = pd.DataFrame([[1, 2, 3, 4]], columns=["name", "name_1", " name ", "NAME"])
    cleaned, _ = _clean_frame(df)
    assert len({name.casefold() for name in cleaned.columns}) == 4
    assert list(cleaned.iloc[0]) == [1, 2, 3, 4]


def test_date_inference_preserves_unparseable_values():
    values = ["2026-01-01"] * 9 + ["not-a-date"]
    cleaned, _ = _clean_frame(pd.DataFrame({"order_date": values}))
    assert cleaned["order_date"].iloc[-1] == "not-a-date"


def test_ambiguous_dates_are_not_silently_changed():
    values = ["November14,2008", "January16,2009"]
    cleaned, _ = _clean_frame(pd.DataFrame({"original_air_date": values}))
    assert cleaned["original_air_date"].tolist() == values


def test_iso_dates_still_infer_as_datetimes():
    cleaned, _ = _clean_frame(pd.DataFrame({"order_date": ["2026-01-01", "2026-02-03"]}))
    assert pd.api.types.is_datetime64_any_dtype(cleaned["order_date"])
