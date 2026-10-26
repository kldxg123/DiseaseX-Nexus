from __future__ import annotations

import datetime as dt

import pytest

from epidemic_core.normalization.dengue import _period
from epidemic_core.normalization.service import NormalizationError


def test_month_isoweek_epiweek_and_derived_periods():
 month=_period({"DATE_TYPE":"month","START_DATE":"2024-02-01","YEAR":2024,"DATE_NUM":2});assert month[:3]==(dt.date(2024,2,1),dt.date(2024,2,29),"month")
 iso=_period({"DATE_TYPE":"isoweek","START_DATE":None,"YEAR":2024,"DATE_NUM":1});assert iso[0]==dt.date(2024,1,1) and iso[1]==dt.date(2024,1,7) and iso[3]
 epi=_period({"DATE_TYPE":"epiweek","START_DATE":"2024-01-07","YEAR":2024,"DATE_NUM":1});assert epi[0]==dt.date(2024,1,7) and epi[1]==dt.date(2024,1,13)


@pytest.mark.parametrize("row",[{"DATE_TYPE":"epiweek","START_DATE":None,"YEAR":2024,"DATE_NUM":1},{"DATE_TYPE":"isoweek","START_DATE":None,"YEAR":2024,"DATE_NUM":54},{"DATE_TYPE":"unknown","START_DATE":"2024-01-01","YEAR":2024,"DATE_NUM":1}])
def test_unresolvable_and_unknown_periods_fail(row):
 with pytest.raises(NormalizationError):_period(row)
