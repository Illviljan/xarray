from __future__ import annotations

from typing import Any, NamedTuple

from xarray.namedarray._array_api._utils import (
    _flattened_dims,
    _get_data_namespace,
)
from xarray.namedarray.core import NamedArray


class UniqueAllResult(NamedTuple):
    values: NamedArray[Any, Any]
    indices: NamedArray[Any, Any]
    inverse_indices: NamedArray[Any, Any]
    counts: NamedArray[Any, Any]


class UniqueCountsResult(NamedTuple):
    values: NamedArray[Any, Any]
    counts: NamedArray[Any, Any]


class UniqueInverseResult(NamedTuple):
    values: NamedArray[Any, Any]
    inverse_indices: NamedArray[Any, Any]


def unique_all(x: NamedArray[Any, Any], /) -> UniqueAllResult:
    xp = _get_data_namespace(x)
    values, indices, inverse_indices, counts = xp.unique_all(x._data)
    _dims = _flattened_dims(x.dims, x.ndim)
    return UniqueAllResult(
        NamedArray(_dims, values),
        NamedArray(_dims, indices),
        NamedArray(_dims, inverse_indices),
        NamedArray(_dims, counts),
    )


def unique_counts(x: NamedArray[Any, Any], /) -> UniqueCountsResult:
    """
    Returns the unique elements of an input array x and the corresponding
    counts for each unique element in x.

    Examples
    --------
    >>> import numpy as np
    >>> x = NamedArray(("x",), np.array([0, 1, 2, 2], dtype=int))
    >>> x_unique = unique_counts(x)
    >>> x_unique.values
    >>> x_unique.counts

    >>> x = NamedArray(("x", "y"), np.array([0, 1, 2, 2], dtype=int).reshape((2, 2)))
    >>> x_unique = unique_counts(x)
    >>> x_unique.values
    >>> x_unique.counts
    """
    xp = _get_data_namespace(x)
    values, counts = xp.unique_counts(x._data)
    _dims = _flattened_dims(x.dims, x.ndim)
    return UniqueCountsResult(
        NamedArray(_dims, values),
        NamedArray(_dims, counts),
    )


def unique_inverse(x: NamedArray[Any, Any], /) -> UniqueInverseResult:
    """
    Returns the unique elements of an input array x and the indices
    from the set of unique elements that reconstruct x.

    Examples
    --------
    >>> import numpy as np
    >>> x = NamedArray(("x",), np.array([0, 1, 2, 2], dtype=int))
    >>> x_unique = unique_inverse(x)
    >>> x_unique.values
    >>> x_unique.counts
    >>> x = NamedArray(("x", "y"), np.array([0, 1, 2, 2], dtype=int).reshape((2, 2)))
    >>> x_unique = unique_inverse(x)
    >>> x_unique.dims, x_unique.shape
    (('x',), (3,))
    """
    xp = _get_data_namespace(x)
    values, inverse_indices = xp.unique_inverse(x._data)
    _dims = _flattened_dims(x.dims, x.ndim)
    return UniqueInverseResult(
        NamedArray(_dims, values),
        NamedArray(_dims, inverse_indices),
    )


def unique_values(x: NamedArray[Any, Any], /) -> NamedArray[Any, Any]:
    """
    Returns the unique elements of an input array x.

    Examples
    --------
    >>> import numpy as np
    >>> x = NamedArray(("x",), np.array([0, 1, 2, 2], dtype=int))
    >>> x_unique = unique_values(x)
    >>> x_unique.dims, x_unique.shape
    (('x',), (3,))
    >>> x = NamedArray(("x", "y"), np.array([0, 1, 2, 2], dtype=int).reshape((2, 2)))
    >>> x_unique = unique_values(x)
    >>> x_unique.dims, x_unique.shape
    (('x',), (3,))
    """
    xp = _get_data_namespace(x)
    _data = xp.unique_values(x._data)
    _dims = _flattened_dims(x.dims, x.ndim)
    return x._new(_dims, _data)
