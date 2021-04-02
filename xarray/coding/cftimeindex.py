"""DatetimeIndex analog for cftime.datetime objects"""
# The pandas.Index subclass defined here was copied and adapted for
# use with cftime.datetime objects based on the source code defining
# pandas.DatetimeIndex.

# For reference, here is a copy of the pandas copyright notice:

# (c) 2011-2012, Lambda Foundry, Inc. and PyData Development Team
# All rights reserved.

# Copyright (c) 2008-2011 AQR Capital Management, LLC
# All rights reserved.

# Redistribution and use in source and binary forms, with or without
# modification, are permitted provided that the following conditions are
# met:

#     * Redistributions of source code must retain the above copyright
#        notice, this list of conditions and the following disclaimer.

#     * Redistributions in binary form must reproduce the above
#        copyright notice, this list of conditions and the following
#        disclaimer in the documentation and/or other materials provided
#        with the distribution.

#     * Neither the name of the copyright holder nor the names of any
#        contributors may be used to endorse or promote products derived
#        from this software without specific prior written permission.

# THIS SOFTWARE IS PROVIDED BY THE COPYRIGHT HOLDER AND CONTRIBUTORS
# "AS IS" AND ANY EXPRESS OR IMPLIED WARRANTIES, INCLUDING, BUT NOT
# LIMITED TO, THE IMPLIED WARRANTIES OF MERCHANTABILITY AND FITNESS FOR
# A PARTICULAR PURPOSE ARE DISCLAIMED. IN NO EVENT SHALL THE COPYRIGHT
# OWNER OR CONTRIBUTORS BE LIABLE FOR ANY DIRECT, INDIRECT, INCIDENTAL,
# SPECIAL, EXEMPLARY, OR CONSEQUENTIAL DAMAGES (INCLUDING, BUT NOT
# LIMITED TO, PROCUREMENT OF SUBSTITUTE GOODS OR SERVICES; LOSS OF USE,
# DATA, OR PROFITS; OR BUSINESS INTERRUPTION) HOWEVER CAUSED AND ON ANY
# THEORY OF LIABILITY, WHETHER IN CONTRACT, STRICT LIABILITY, OR TORT
# (INCLUDING NEGLIGENCE OR OTHERWISE) ARISING IN ANY WAY OUT OF THE USE
# OF THIS SOFTWARE, EVEN IF ADVISED OF THE POSSIBILITY OF SUCH DAMAGE.

import re
import warnings
from datetime import timedelta
from distutils.version import LooseVersion

import numpy as np
import pandas as pd

from xarray.core.utils import is_scalar

from ..core.common import _contains_cftime_datetimes
from ..core.options import OPTIONS
from .times import _STANDARD_CALENDARS, cftime_to_nptime, infer_calendar_name

# constants for cftimeindex.repr
CFTIME_REPR_LENGTH = 19
ITEMS_IN_REPR_MAX_ELSE_ELLIPSIS = 100
REPR_ELLIPSIS_SHOW_ITEMS_FRONT_END = 10


if LooseVersion(pd.__version__) > LooseVersion("1.2.3"):
    OUT_OF_BOUNDS_TIMEDELTA_ERROR = pd.errors.OutOfBoundsTimedelta
else:
    OUT_OF_BOUNDS_TIMEDELTA_ERROR = OverflowError


def named(name, pattern):
    return "(?P<" + name + ">" + pattern + ")"


def optional(x):
    return "(?:" + x + ")?"


def trailing_optional(xs):
    if not xs:
        return ""
    return xs[0] + optional(trailing_optional(xs[1:]))


def build_pattern(date_sep=r"\-", datetime_sep=r"T", time_sep=r"\:"):
    pieces = [
        (None, "year", r"\d{4}"),
        (date_sep, "month", r"\d{2}"),
        (date_sep, "day", r"\d{2}"),
        (datetime_sep, "hour", r"\d{2}"),
        (time_sep, "minute", r"\d{2}"),
        (time_sep, "second", r"\d{2}"),
    ]
    pattern_list = []
    for sep, name, sub_pattern in pieces:
        pattern_list.append((sep if sep else "") + named(name, sub_pattern))
        # TODO: allow timezone offsets?
    return "^" + trailing_optional(pattern_list) + "$"


_BASIC_PATTERN = build_pattern(date_sep="", time_sep="")
_EXTENDED_PATTERN = build_pattern()
_CFTIME_PATTERN = build_pattern(datetime_sep=" ")
_PATTERNS = [_BASIC_PATTERN, _EXTENDED_PATTERN, _CFTIME_PATTERN]


def parse_iso8601_like(datetime_string):
    for pattern in _PATTERNS:
        match = re.match(pattern, datetime_string)
        if match:
            return match.groupdict()
    raise ValueError(
        f"no ISO-8601 or cftime-string-like match for string: {datetime_string}"
    )


def _parse_iso8601_with_reso(date_type, timestr):
    import cftime

    default = date_type(1, 1, 1)
    result = parse_iso8601_like(timestr)
    replace = {}

    for attr in ["year", "month", "day", "hour", "minute", "second"]:
        value = result.get(attr, None)
        if value is not None:
            # Note ISO8601 conventions allow for fractional seconds.
            # TODO: Consider adding support for sub-second resolution?
            replace[attr] = int(value)
            resolution = attr
    if LooseVersion(cftime.__version__) < LooseVersion("1.0.4"):
        # dayofwk=-1 is required to update the dayofwk and dayofyr attributes of
        # the returned date object in versions of cftime between 1.0.2 and
        # 1.0.3.4.  It can be removed for versions of cftime greater than
        # 1.0.3.4.
        replace["dayofwk"] = -1
    return default.replace(**replace), resolution


def _parsed_string_to_bounds(date_type, resolution, parsed):
    """Generalization of
    pandas.tseries.index.DatetimeIndex._parsed_string_to_bounds
    for use with non-standard calendars and cftime.datetime
    objects.
    """
    if resolution == "year":
        return (
            date_type(parsed.year, 1, 1),
            date_type(parsed.year + 1, 1, 1) - timedelta(microseconds=1),
        )
    elif resolution == "month":
        if parsed.month == 12:
            end = date_type(parsed.year + 1, 1, 1) - timedelta(microseconds=1)
        else:
            end = date_type(parsed.year, parsed.month + 1, 1) - timedelta(
                microseconds=1
            )
        return date_type(parsed.year, parsed.month, 1), end
    elif resolution == "day":
        start = date_type(parsed.year, parsed.month, parsed.day)
        return start, start + timedelta(days=1, microseconds=-1)
    elif resolution == "hour":
        start = date_type(parsed.year, parsed.month, parsed.day, parsed.hour)
        return start, start + timedelta(hours=1, microseconds=-1)
    elif resolution == "minute":
        start = date_type(
            parsed.year, parsed.month, parsed.day, parsed.hour, parsed.minute
        )
        return start, start + timedelta(minutes=1, microseconds=-1)
    elif resolution == "second":
        start = date_type(
            parsed.year,
            parsed.month,
            parsed.day,
            parsed.hour,
            parsed.minute,
            parsed.second,
        )
        return start, start + timedelta(seconds=1, microseconds=-1)
    else:
        raise KeyError


def get_date_field(datetimes, field):
    """Adapted from pandas.tslib.get_date_field"""
    return np.array([getattr(date, field) for date in datetimes])


def _field_accessor(name, docstring=None, min_cftime_version="0.0"):
    """Adapted from pandas.tseries.index._field_accessor"""

    def f(self, min_cftime_version=min_cftime_version):
        import cftime

        version = cftime.__version__

        if LooseVersion(version) >= LooseVersion(min_cftime_version):
            return get_date_field(self._data, name)
        else:
            raise ImportError(
                "The {!r} accessor requires a minimum "
                "version of cftime of {}. Found an "
                "installed version of {}.".format(name, min_cftime_version, version)
            )

    f.__name__ = name
    f.__doc__ = docstring
    return property(f)


def get_date_type(self):
    if self._data.size:
        return type(self._data[0])
    else:
        return None


def assert_all_valid_date_type(data):
    import cftime

    if len(data) > 0:
        sample = data[0]
        date_type = type(sample)
        if not isinstance(sample, cftime.datetime):
            raise TypeError(
                "CFTimeIndex requires cftime.datetime "
                "objects. Got object of {}.".format(date_type)
            )
        if not all(isinstance(value, date_type) for value in data):
            raise TypeError(
                "CFTimeIndex requires using datetime "
                "objects of all the same type.  Got\n{}.".format(data)
            )


def format_row(times, indent=0, separator=", ", row_end=",\n"):
    """Format a single row from format_times."""
    return indent * " " + separator.join(map(str, times)) + row_end


def format_times(
    index,
    max_width,
    offset,
    separator=", ",
    first_row_offset=0,
    intermediate_row_end=",\n",
    last_row_end="",
):
    """Format values of cftimeindex as pd.Index."""
    n_per_row = max(max_width // (CFTIME_REPR_LENGTH + len(separator)), 1)
    n_rows = int(np.ceil(len(index) / n_per_row))

    representation = ""
    for row in range(n_rows):
        indent = first_row_offset if row == 0 else offset
        row_end = last_row_end if row == n_rows - 1 else intermediate_row_end
        times_for_row = index[row * n_per_row : (row + 1) * n_per_row]
        representation = representation + format_row(
            times_for_row, indent=indent, separator=separator, row_end=row_end
        )

    return representation


def format_attrs(index, separator=", "):
    """Format attributes of CFTimeIndex for __repr__."""
    attrs = {
        "dtype": f"'{index.dtype}'",
        "length": f"{len(index)}",
        "calendar": f"'{index.calendar}'",
    }
    attrs["freq"] = f"'{index.freq}'" if len(index) >= 3 else None
    attrs_str = [f"{k}={v}" for k, v in attrs.items()]
    attrs_str = f"{separator}".join(attrs_str)
    return attrs_str


class CFTimeIndex(pd.Index):
    """Custom Index for working with CF calendars and dates

    All elements of a CFTimeIndex must be cftime.datetime objects.

    Parameters
    ----------
    data : array or CFTimeIndex
        Sequence of cftime.datetime objects to use in index
    name : str, default: None
        Name of the resulting index

    See Also
    --------
    cftime_range
    """

    year = _field_accessor("year", "The year of the datetime")
    month = _field_accessor("month", "The month of the datetime")
    day = _field_accessor("day", "The days of the datetime")
    hour = _field_accessor("hour", "The hours of the datetime")
    minute = _field_accessor("minute", "The minutes of the datetime")
    second = _field_accessor("second", "The seconds of the datetime")
    microsecond = _field_accessor("microsecond", "The microseconds of the datetime")
    dayofyear = _field_accessor(
        "dayofyr", "The ordinal day of year of the datetime", "1.0.2.1"
    )
    dayofweek = _field_accessor("dayofwk", "The day of week of the datetime", "1.0.2.1")
    days_in_month = _field_accessor(
        "daysinmonth", "The number of days in the month of the datetime", "1.1.0.0"
    )
    date_type = property(get_date_type)

    def __new__(cls, data, name=None):
        assert_all_valid_date_type(data)
        if name is None and hasattr(data, "name"):
            name = data.name

        result = object.__new__(cls)
        result._data = np.array(data, dtype="O")
        result.name = name
        result._cache = {}
        return result

    def __repr__(self):
        """
        Return a string representation for this object.
        """
        klass_name = type(self).__name__
        display_width = OPTIONS["display_width"]
        offset = len(klass_name) + 2

        if len(self) <= ITEMS_IN_REPR_MAX_ELSE_ELLIPSIS:
            datastr = format_times(
                self.values, display_width, offset=offset, first_row_offset=0
            )
        else:
            front_str = format_times(
                self.values[:REPR_ELLIPSIS_SHOW_ITEMS_FRONT_END],
                display_width,
                offset=offset,
                first_row_offset=0,
                last_row_end=",",
            )
            end_str = format_times(
                self.values[-REPR_ELLIPSIS_SHOW_ITEMS_FRONT_END:],
                display_width,
                offset=offset,
                first_row_offset=offset,
            )
            datastr = "\n".join([front_str, f"{' '*offset}...", end_str])

        attrs_str = format_attrs(self)
        # oneliner only if smaller than display_width
        full_repr_str = f"{klass_name}([{datastr}], {attrs_str})"
        if len(full_repr_str) <= display_width:
            return full_repr_str
        else:
            # if attrs_str too long, one per line
            if len(attrs_str) >= display_width - offset:
                attrs_str = attrs_str.replace(",", f",\n{' '*(offset-2)}")
            full_repr_str = f"{klass_name}([{datastr}],\n{' '*(offset-1)}{attrs_str})"
            return full_repr_str

    def _partial_date_slice(self, resolution, parsed):
        """Adapted from
        pandas.tseries.index.DatetimeIndex._partial_date_slice

        Note that when using a CFTimeIndex, if a partial-date selection
        returns a single element, it will never be converted to a scalar
        coordinate; this is in slight contrast to the behavior when using
        a DatetimeIndex, which sometimes will return a DataArray with a scalar
        coordinate depending on the resolution of the datetimes used in
        defining the index.  For example:

        >>> from cftime import DatetimeNoLeap
        >>> import pandas as pd
        >>> import xarray as xr
        >>> da = xr.DataArray(
        ...     [1, 2],
        ...     coords=[[DatetimeNoLeap(2001, 1, 1), DatetimeNoLeap(2001, 2, 1)]],
        ...     dims=["time"],
        ... )
        >>> da.sel(time="2001-01-01")
        <xarray.DataArray (time: 1)>
        array([1])
        Coordinates:
          * time     (time) object 2001-01-01 00:00:00
        >>> da = xr.DataArray(
        ...     [1, 2],
        ...     coords=[[pd.Timestamp(2001, 1, 1), pd.Timestamp(2001, 2, 1)]],
        ...     dims=["time"],
        ... )
        >>> da.sel(time="2001-01-01")
        <xarray.DataArray ()>
        array(1)
        Coordinates:
            time     datetime64[ns] 2001-01-01
        >>> da = xr.DataArray(
        ...     [1, 2],
        ...     coords=[[pd.Timestamp(2001, 1, 1, 1), pd.Timestamp(2001, 2, 1)]],
        ...     dims=["time"],
        ... )
        >>> da.sel(time="2001-01-01")
        <xarray.DataArray (time: 1)>
        array([1])
        Coordinates:
          * time     (time) datetime64[ns] 2001-01-01T01:00:00
        """
        start, end = _parsed_string_to_bounds(self.date_type, resolution, parsed)

        times = self._data

        if self.is_monotonic:
            if len(times) and (
                (start < times[0] and end < times[0])
                or (start > times[-1] and end > times[-1])
            ):
                # we are out of range
                raise KeyError

            # a monotonic (sorted) series can be sliced
            left = times.searchsorted(start, side="left")
            right = times.searchsorted(end, side="right")
            return slice(left, right)

        lhs_mask = times >= start
        rhs_mask = times <= end
        return np.flatnonzero(lhs_mask & rhs_mask)

    def _get_string_slice(self, key):
        """Adapted from pandas.tseries.index.DatetimeIndex._get_string_slice"""
        parsed, resolution = _parse_iso8601_with_reso(self.date_type, key)
        try:
            loc = self._partial_date_slice(resolution, parsed)
        except KeyError:
            raise KeyError(key)
        return loc

    def _get_nearest_indexer(self, target, limit, tolerance):
        """Adapted from pandas.Index._get_nearest_indexer"""
        left_indexer = self.get_indexer(target, "pad", limit=limit)
        right_indexer = self.get_indexer(target, "backfill", limit=limit)
        left_distances = abs(self.values[left_indexer] - target.values)
        right_distances = abs(self.values[right_indexer] - target.values)

        if self.is_monotonic_increasing:
            condition = (left_distances < right_distances) | (right_indexer == -1)
        else:
            condition = (left_distances <= right_distances) | (right_indexer == -1)
        indexer = np.where(condition, left_indexer, right_indexer)

        if tolerance is not None:
            indexer = self._filter_indexer_tolerance(target, indexer, tolerance)
        return indexer

    def _filter_indexer_tolerance(self, target, indexer, tolerance):
        """Adapted from pandas.Index._filter_indexer_tolerance"""
        if isinstance(target, pd.Index):
            distance = abs(self.values[indexer] - target.values)
        else:
            distance = abs(self.values[indexer] - target)
        indexer = np.where(distance <= tolerance, indexer, -1)
        return indexer

    def get_loc(self, key, method=None, tolerance=None):
        """Adapted from pandas.tseries.index.DatetimeIndex.get_loc"""
        if isinstance(key, str):
            return self._get_string_slice(key)
        else:
            return pd.Index.get_loc(self, key, method=method, tolerance=tolerance)

    def _maybe_cast_slice_bound(self, label, side, kind):
        """Adapted from
        pandas.tseries.index.DatetimeIndex._maybe_cast_slice_bound"""
        if isinstance(label, str):
            parsed, resolution = _parse_iso8601_with_reso(self.date_type, label)
            start, end = _parsed_string_to_bounds(self.date_type, resolution, parsed)
            if self.is_monotonic_decreasing and len(self) > 1:
                return end if side == "left" else start
            return start if side == "left" else end
        else:
            return label

    # TODO: Add ability to use integer range outside of iloc?
    # e.g. series[1:5].
    def get_value(self, series, key):
        """Adapted from pandas.tseries.index.DatetimeIndex.get_value"""
        if np.asarray(key).dtype == np.dtype(bool):
            return series.iloc[key]
        elif isinstance(key, slice):
            return series.iloc[self.slice_indexer(key.start, key.stop, key.step)]
        else:
            return series.iloc[self.get_loc(key)]

    def __contains__(self, key):
        """Adapted from
        pandas.tseries.base.DatetimeIndexOpsMixin.__contains__"""
        try:
            result = self.get_loc(key)
            return (
                is_scalar(result)
                or type(result) == slice
                or (isinstance(result, np.ndarray) and result.size)
            )
        except (KeyError, TypeError, ValueError):
            return False

    def contains(self, key):
        """Needed for .loc based partial-string indexing"""
        return self.__contains__(key)

    def shift(self, n, freq):
        """Shift the CFTimeIndex a multiple of the given frequency.

        See the documentation for :py:func:`~xarray.cftime_range` for a
        complete listing of valid frequency strings.

        Parameters
        ----------
        n : int
            Periods to shift by
        freq : str or datetime.timedelta
            A frequency string or datetime.timedelta object to shift by

        Returns
        -------
        CFTimeIndex

        See Also
        --------
        pandas.DatetimeIndex.shift

        Examples
        --------
        >>> index = xr.cftime_range("2000", periods=1, freq="M")
        >>> index
        CFTimeIndex([2000-01-31 00:00:00],
                    dtype='object', length=1, calendar='gregorian', freq=None)
        >>> index.shift(1, "M")
        CFTimeIndex([2000-02-29 00:00:00],
                    dtype='object', length=1, calendar='gregorian', freq=None)
        """
        from .cftime_offsets import to_offset

        if not isinstance(n, int):
            raise TypeError(f"'n' must be an int, got {n}.")
        if isinstance(freq, timedelta):
            return self + n * freq
        elif isinstance(freq, str):
            return self + n * to_offset(freq)
        else:
            raise TypeError(
                "'freq' must be of type "
                "str or datetime.timedelta, got {}.".format(freq)
            )

    def __add__(self, other):
        if isinstance(other, pd.TimedeltaIndex):
            other = other.to_pytimedelta()
        return CFTimeIndex(np.array(self) + other)

    def __radd__(self, other):
        if isinstance(other, pd.TimedeltaIndex):
            other = other.to_pytimedelta()
        return CFTimeIndex(other + np.array(self))

    def __sub__(self, other):
        if _contains_datetime_timedeltas(other):
            return CFTimeIndex(np.array(self) - other)
        elif isinstance(other, pd.TimedeltaIndex):
            return CFTimeIndex(np.array(self) - other.to_pytimedelta())
        elif _contains_cftime_datetimes(np.array(other)):
            try:
                return pd.TimedeltaIndex(np.array(self) - np.array(other))
            except OUT_OF_BOUNDS_TIMEDELTA_ERROR:
                raise ValueError(
                    "The time difference exceeds the range of values "
                    "that can be expressed at the nanosecond resolution."
                )
        else:
            return NotImplemented

    def __rsub__(self, other):
        try:
            return pd.TimedeltaIndex(other - np.array(self))
        except OUT_OF_BOUNDS_TIMEDELTA_ERROR:
            raise ValueError(
                "The time difference exceeds the range of values "
                "that can be expressed at the nanosecond resolution."
            )

    def to_datetimeindex(self, unsafe=False):
        """If possible, convert this index to a pandas.DatetimeIndex.

        Parameters
        ----------
        unsafe : bool
            Flag to turn off warning when converting from a CFTimeIndex with
            a non-standard calendar to a DatetimeIndex (default ``False``).

        Returns
        -------
        pandas.DatetimeIndex

        Raises
        ------
        ValueError
            If the CFTimeIndex contains dates that are not possible in the
            standard calendar or outside the pandas.Timestamp-valid range.

        Warns
        -----
        RuntimeWarning
            If converting from a non-standard calendar to a DatetimeIndex.

        Warnings
        --------
        Note that for non-standard calendars, this will change the calendar
        type of the index.  In that case the result of this method should be
        used with caution.

        Examples
        --------
        >>> import xarray as xr
        >>> times = xr.cftime_range("2000", periods=2, calendar="gregorian")
        >>> times
        CFTimeIndex([2000-01-01 00:00:00, 2000-01-02 00:00:00],
                    dtype='object', length=2, calendar='gregorian', freq=None)
        >>> times.to_datetimeindex()
        DatetimeIndex(['2000-01-01', '2000-01-02'], dtype='datetime64[ns]', freq=None)
        """
        nptimes = cftime_to_nptime(self)
        calendar = infer_calendar_name(self)
        if calendar not in _STANDARD_CALENDARS and not unsafe:
            warnings.warn(
                "Converting a CFTimeIndex with dates from a non-standard "
                "calendar, {!r}, to a pandas.DatetimeIndex, which uses dates "
                "from the standard calendar.  This may lead to subtle errors "
                "in operations that depend on the length of time between "
                "dates.".format(calendar),
                RuntimeWarning,
                stacklevel=2,
            )
        return pd.DatetimeIndex(nptimes)

    def strftime(self, date_format):
        """
        Return an Index of formatted strings specified by date_format, which
        supports the same string format as the python standard library. Details
        of the string format can be found in `python string format doc
        <https://docs.python.org/3/library/datetime.html#strftime-strptime-behavior>`__

        Parameters
        ----------
        date_format : str
            Date format string (e.g. "%Y-%m-%d")

        Returns
        -------
        pandas.Index
            Index of formatted strings

        Examples
        --------
        >>> rng = xr.cftime_range(
        ...     start="2000", periods=5, freq="2MS", calendar="noleap"
        ... )
        >>> rng.strftime("%B %d, %Y, %r")
        Index(['January 01, 2000, 12:00:00 AM', 'March 01, 2000, 12:00:00 AM',
               'May 01, 2000, 12:00:00 AM', 'July 01, 2000, 12:00:00 AM',
               'September 01, 2000, 12:00:00 AM'],
              dtype='object')
        """
        return pd.Index([date.strftime(date_format) for date in self._data])

    @property
    def asi8(self):
        """Convert to integers with units of microseconds since 1970-01-01."""
        from ..core.resample_cftime import exact_cftime_datetime_difference

        epoch = self.date_type(1970, 1, 1)
        return np.array(
            [
                _total_microseconds(exact_cftime_datetime_difference(epoch, date))
                for date in self.values
            ],
            dtype=np.int64,
        )

    @property
    def calendar(self):
        """The calendar used by the datetimes in the index."""
        from .times import infer_calendar_name

        return infer_calendar_name(self)

    @property
    def freq(self):
        """The frequency used by the dates in the index."""
        from .frequencies import infer_freq

        return infer_freq(self)

    def _round_via_method(self, freq, method):
        """Round dates using a specified method."""
        from .cftime_offsets import CFTIME_TICKS, to_offset

        offset = to_offset(freq)
        if not isinstance(offset, CFTIME_TICKS):
            raise ValueError(f"{offset} is a non-fixed frequency")

        unit = _total_microseconds(offset.as_timedelta())
        values = self.asi8
        rounded = method(values, unit)
        return _cftimeindex_from_i8(rounded, self.date_type, self.name)

    def floor(self, freq):
        """Round dates down to fixed frequency.

        Parameters
        ----------
        freq : str
            The frequency level to round the index to.  Must be a fixed
            frequency like 'S' (second) not 'ME' (month end).  See `frequency
            aliases <https://pandas.pydata.org/pandas-docs/stable/user_guide/timeseries.html#offset-aliases>`_
            for a list of possible values.

        Returns
        -------
        CFTimeIndex
        """
        return self._round_via_method(freq, _floor_int)

    def ceil(self, freq):
        """Round dates up to fixed frequency.

        Parameters
        ----------
        freq : str
            The frequency level to round the index to.  Must be a fixed
            frequency like 'S' (second) not 'ME' (month end).  See `frequency
            aliases <https://pandas.pydata.org/pandas-docs/stable/user_guide/timeseries.html#offset-aliases>`_
            for a list of possible values.

        Returns
        -------
        CFTimeIndex
        """
        return self._round_via_method(freq, _ceil_int)

    def round(self, freq):
        """Round dates to a fixed frequency.

        Parameters
        ----------
        freq : str
            The frequency level to round the index to.  Must be a fixed
            frequency like 'S' (second) not 'ME' (month end).  See `frequency
            aliases <https://pandas.pydata.org/pandas-docs/stable/user_guide/timeseries.html#offset-aliases>`_
            for a list of possible values.

        Returns
        -------
        CFTimeIndex
        """
        return self._round_via_method(freq, _round_to_nearest_half_even)


def _parse_iso8601_without_reso(date_type, datetime_str):
    date, _ = _parse_iso8601_with_reso(date_type, datetime_str)
    return date


def _parse_array_of_cftime_strings(strings, date_type):
    """Create a numpy array from an array of strings.

    For use in generating dates from strings for use with interp.  Assumes the
    array is either 0-dimensional or 1-dimensional.

    Parameters
    ----------
    strings : array of strings
        Strings to convert to dates
    date_type : cftime.datetime type
        Calendar type to use for dates

    Returns
    -------
    np.array
    """
    return np.array(
        [_parse_iso8601_without_reso(date_type, s) for s in strings.ravel()]
    ).reshape(strings.shape)


def _contains_datetime_timedeltas(array):
    """Check if an input array contains datetime.timedelta objects."""
    array = np.atleast_1d(array)
    return isinstance(array[0], timedelta)


def _cftimeindex_from_i8(values, date_type, name):
    """Construct a CFTimeIndex from an array of integers.

    Parameters
    ----------
    values : np.array
        Integers representing microseconds since 1970-01-01.
    date_type : cftime.datetime
        Type of date for the index.
    name : str
        Name of the index.

    Returns
    -------
    CFTimeIndex
    """
    epoch = date_type(1970, 1, 1)
    dates = np.array([epoch + timedelta(microseconds=int(value)) for value in values])
    return CFTimeIndex(dates, name=name)


def _total_microseconds(delta):
    """Compute the total number of microseconds of a datetime.timedelta.

    Parameters
    ----------
    delta : datetime.timedelta
        Input timedelta.

    Returns
    -------
    int
    """
    return delta / timedelta(microseconds=1)


def _floor_int(values, unit):
    """Copied from pandas."""
    return values - np.remainder(values, unit)


def _ceil_int(values, unit):
    """Copied from pandas."""
    return values + np.remainder(-values, unit)


def _round_to_nearest_half_even(values, unit):
    """Copied from pandas."""
    if unit % 2:
        return _ceil_int(values - unit // 2, unit)
    quotient, remainder = np.divmod(values, unit)
    mask = np.logical_or(
        remainder > (unit // 2), np.logical_and(remainder == (unit // 2), quotient % 2)
    )
    quotient[mask] += 1
    return quotient * unit
