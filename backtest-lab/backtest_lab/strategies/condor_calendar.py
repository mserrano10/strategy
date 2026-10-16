"""FOMC decision days and CPI release mornings for the condor week filter.

FOMC dates are the announcement day (the second day of a two-day meeting,
or the day of a one-day meeting). They were taken from the Federal Reserve's
historical calendars. March 3 and March 15, 2020 are the emergency meetings.
August 27, 2020 is the strategy-statement day.

CPI dates are the Bureau of Labor Statistics 8:30 a.m. Eastern release.
2024 was checked against the published BLS schedule. Other years are the
compiled release calendar used by this study. A one-day miss usually stays
inside the same Monday–Friday week; the page says so.
"""

from __future__ import annotations

from datetime import date

# Announcement day, not the first day of a two-day meeting.
FOMC_DECISIONS = (
    date(2010, 1, 27), date(2010, 3, 16), date(2010, 4, 28), date(2010, 6, 23),
    date(2010, 8, 10), date(2010, 9, 21), date(2010, 11, 3), date(2010, 12, 14),
    date(2011, 1, 26), date(2011, 3, 15), date(2011, 4, 27), date(2011, 6, 22),
    date(2011, 8, 9), date(2011, 9, 21), date(2011, 11, 2), date(2011, 12, 13),
    date(2012, 1, 25), date(2012, 3, 13), date(2012, 4, 25), date(2012, 6, 20),
    date(2012, 8, 1), date(2012, 9, 13), date(2012, 10, 24), date(2012, 12, 12),
    date(2013, 1, 30), date(2013, 3, 20), date(2013, 5, 1), date(2013, 6, 19),
    date(2013, 7, 31), date(2013, 9, 18), date(2013, 10, 30), date(2013, 12, 18),
    date(2014, 1, 29), date(2014, 3, 19), date(2014, 4, 30), date(2014, 6, 18),
    date(2014, 7, 30), date(2014, 9, 17), date(2014, 10, 29), date(2014, 12, 17),
    date(2015, 1, 28), date(2015, 3, 18), date(2015, 4, 29), date(2015, 6, 17),
    date(2015, 7, 29), date(2015, 9, 17), date(2015, 10, 28), date(2015, 12, 16),
    date(2016, 1, 27), date(2016, 3, 16), date(2016, 4, 27), date(2016, 6, 15),
    date(2016, 7, 27), date(2016, 9, 21), date(2016, 11, 2), date(2016, 12, 14),
    date(2017, 2, 1), date(2017, 3, 15), date(2017, 5, 3), date(2017, 6, 14),
    date(2017, 7, 26), date(2017, 9, 20), date(2017, 11, 1), date(2017, 12, 13),
    date(2018, 1, 31), date(2018, 3, 21), date(2018, 5, 2), date(2018, 6, 13),
    date(2018, 8, 1), date(2018, 9, 26), date(2018, 11, 8), date(2018, 12, 19),
    date(2019, 1, 30), date(2019, 3, 20), date(2019, 5, 1), date(2019, 6, 19),
    date(2019, 7, 31), date(2019, 9, 18), date(2019, 10, 30), date(2019, 12, 11),
    date(2020, 1, 29), date(2020, 3, 3), date(2020, 3, 15), date(2020, 4, 29),
    date(2020, 6, 10), date(2020, 7, 29), date(2020, 8, 27), date(2020, 9, 16),
    date(2020, 11, 5), date(2020, 12, 16),
    date(2021, 1, 27), date(2021, 3, 17), date(2021, 4, 28), date(2021, 6, 16),
    date(2021, 7, 28), date(2021, 9, 22), date(2021, 11, 3), date(2021, 12, 15),
    date(2022, 1, 26), date(2022, 3, 16), date(2022, 5, 4), date(2022, 6, 15),
    date(2022, 7, 27), date(2022, 9, 21), date(2022, 11, 2), date(2022, 12, 14),
    date(2023, 2, 1), date(2023, 3, 22), date(2023, 5, 3), date(2023, 6, 14),
    date(2023, 7, 26), date(2023, 9, 20), date(2023, 11, 1), date(2023, 12, 13),
    date(2024, 1, 31), date(2024, 3, 20), date(2024, 5, 1), date(2024, 6, 12),
    date(2024, 7, 31), date(2024, 9, 18), date(2024, 11, 7), date(2024, 12, 18),
    date(2025, 1, 29), date(2025, 3, 19), date(2025, 5, 7), date(2025, 6, 18),
    date(2025, 7, 30), date(2025, 9, 17), date(2025, 10, 29), date(2025, 12, 10),
    date(2026, 1, 28), date(2026, 3, 18), date(2026, 4, 29), date(2026, 6, 17),
    date(2026, 7, 29), date(2026, 9, 16), date(2026, 10, 28), date(2026, 12, 9),
)

# 2024 checked against bls.gov/schedule/2024. The rest is the compiled release list.
CPI_RELEASES = (
    date(2010, 1, 15), date(2010, 2, 19), date(2010, 3, 18), date(2010, 4, 14),
    date(2010, 5, 19), date(2010, 6, 17), date(2010, 7, 16), date(2010, 8, 13),
    date(2010, 9, 17), date(2010, 10, 15), date(2010, 11, 17), date(2010, 12, 15),
    date(2011, 1, 14), date(2011, 2, 17), date(2011, 3, 17), date(2011, 4, 15),
    date(2011, 5, 13), date(2011, 6, 15), date(2011, 7, 15), date(2011, 8, 18),
    date(2011, 9, 15), date(2011, 10, 19), date(2011, 11, 16), date(2011, 12, 16),
    date(2012, 1, 19), date(2012, 2, 17), date(2012, 3, 16), date(2012, 4, 13),
    date(2012, 5, 15), date(2012, 6, 14), date(2012, 7, 17), date(2012, 8, 15),
    date(2012, 9, 14), date(2012, 10, 16), date(2012, 11, 15), date(2012, 12, 14),
    date(2013, 1, 16), date(2013, 2, 21), date(2013, 3, 15), date(2013, 4, 16),
    date(2013, 5, 16), date(2013, 6, 18), date(2013, 7, 16), date(2013, 8, 15),
    date(2013, 9, 17), date(2013, 10, 30), date(2013, 11, 20), date(2013, 12, 17),
    date(2014, 1, 16), date(2014, 2, 20), date(2014, 3, 18), date(2014, 4, 15),
    date(2014, 5, 15), date(2014, 6, 17), date(2014, 7, 22), date(2014, 8, 19),
    date(2014, 9, 17), date(2014, 10, 22), date(2014, 11, 20), date(2014, 12, 17),
    date(2015, 1, 16), date(2015, 2, 26), date(2015, 3, 24), date(2015, 4, 17),
    date(2015, 5, 22), date(2015, 6, 18), date(2015, 7, 17), date(2015, 8, 19),
    date(2015, 9, 16), date(2015, 10, 15), date(2015, 11, 17), date(2015, 12, 15),
    date(2016, 1, 20), date(2016, 2, 19), date(2016, 3, 16), date(2016, 4, 14),
    date(2016, 5, 17), date(2016, 6, 16), date(2016, 7, 15), date(2016, 8, 16),
    date(2016, 9, 16), date(2016, 10, 18), date(2016, 11, 17), date(2016, 12, 15),
    date(2017, 1, 18), date(2017, 2, 15), date(2017, 3, 15), date(2017, 4, 14),
    date(2017, 5, 12), date(2017, 6, 14), date(2017, 7, 14), date(2017, 8, 11),
    date(2017, 9, 14), date(2017, 10, 13), date(2017, 11, 15), date(2017, 12, 13),
    date(2018, 1, 12), date(2018, 2, 14), date(2018, 3, 13), date(2018, 4, 11),
    date(2018, 5, 10), date(2018, 6, 12), date(2018, 7, 12), date(2018, 8, 10),
    date(2018, 9, 13), date(2018, 10, 11), date(2018, 11, 14), date(2018, 12, 12),
    date(2019, 1, 11), date(2019, 2, 13), date(2019, 3, 12), date(2019, 4, 10),
    date(2019, 5, 10), date(2019, 6, 12), date(2019, 7, 11), date(2019, 8, 13),
    date(2019, 9, 12), date(2019, 10, 10), date(2019, 11, 13), date(2019, 12, 11),
    date(2020, 1, 14), date(2020, 2, 13), date(2020, 3, 11), date(2020, 4, 10),
    date(2020, 5, 12), date(2020, 6, 10), date(2020, 7, 14), date(2020, 8, 12),
    date(2020, 9, 11), date(2020, 10, 13), date(2020, 11, 12), date(2020, 12, 10),
    date(2021, 1, 13), date(2021, 2, 10), date(2021, 3, 10), date(2021, 4, 13),
    date(2021, 5, 12), date(2021, 6, 10), date(2021, 7, 13), date(2021, 8, 11),
    date(2021, 9, 14), date(2021, 10, 13), date(2021, 11, 10), date(2021, 12, 10),
    date(2022, 1, 12), date(2022, 2, 10), date(2022, 3, 10), date(2022, 4, 12),
    date(2022, 5, 11), date(2022, 6, 10), date(2022, 7, 13), date(2022, 8, 10),
    date(2022, 9, 13), date(2022, 10, 13), date(2022, 11, 10), date(2022, 12, 13),
    date(2023, 1, 12), date(2023, 2, 14), date(2023, 3, 14), date(2023, 4, 12),
    date(2023, 5, 10), date(2023, 6, 13), date(2023, 7, 12), date(2023, 8, 10),
    date(2023, 9, 13), date(2023, 10, 12), date(2023, 11, 14), date(2023, 12, 12),
    date(2024, 1, 11), date(2024, 2, 13), date(2024, 3, 12), date(2024, 4, 10),
    date(2024, 5, 15), date(2024, 6, 12), date(2024, 7, 11), date(2024, 8, 14),
    date(2024, 9, 11), date(2024, 10, 10), date(2024, 11, 13), date(2024, 12, 11),
    date(2025, 1, 15), date(2025, 2, 12), date(2025, 3, 12), date(2025, 4, 10),
    date(2025, 5, 13), date(2025, 6, 11), date(2025, 7, 15), date(2025, 8, 12),
    date(2025, 9, 11), date(2025, 10, 24), date(2025, 11, 13), date(2025, 12, 18),
    date(2026, 1, 13), date(2026, 2, 11), date(2026, 3, 11), date(2026, 4, 10),
    date(2026, 5, 12), date(2026, 6, 10), date(2026, 7, 14), date(2026, 8, 12),
    date(2026, 9, 11), date(2026, 10, 14),
)

FOMC = set(FOMC_DECISIONS)
CPI = set(CPI_RELEASES)
