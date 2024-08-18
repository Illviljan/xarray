from __future__ import annotations

from types import ModuleType
from typing import Any

import numpy as np

from xarray.namedarray._typing import (
    Default,
    _arrayapi,
    _Axes,
    _Axis,
    _AxisLike,
    _default,
    _Dim,
    _Dims,
    _DType,
    _ScalarType,
    _ShapeType,
    _SupportsImag,
    _SupportsReal,
)
from xarray.namedarray.core import (
    NamedArray,
    _dims_to_axis,
    _get_remaining_dims,
)


def _get_data_namespace(x: NamedArray[Any, Any]) -> ModuleType:
    if isinstance(x._data, _arrayapi):
        return x._data.__array_namespace__()

    return np


# %% Dtypes
# TODO: should delegate to underlying array? Cubed doesn't at the moment.
int8 = np.int8
int16 = np.int16
int32 = np.int32
int64 = np.int64
uint8 = np.uint8
uint16 = np.uint16
uint32 = np.uint32
uint64 = np.uint64
float32 = np.float32
float64 = np.float64
complex64 = np.complex64
complex128 = np.complex128
bool = np.bool

_all_dtypes = (
    int8,
    int16,
    int32,
    int64,
    uint8,
    uint16,
    uint32,
    uint64,
    float32,
    float64,
    complex64,
    complex128,
    bool,
)
_boolean_dtypes = (bool,)
_real_floating_dtypes = (float32, float64)
_floating_dtypes = (float32, float64, complex64, complex128)
_complex_floating_dtypes = (complex64, complex128)
_integer_dtypes = (int8, int16, int32, int64, uint8, uint16, uint32, uint64)
_signed_integer_dtypes = (int8, int16, int32, int64)
_unsigned_integer_dtypes = (uint8, uint16, uint32, uint64)
_integer_or_boolean_dtypes = (
    bool,
    int8,
    int16,
    int32,
    int64,
    uint8,
    uint16,
    uint32,
    uint64,
)
_real_numeric_dtypes = (
    float32,
    float64,
    int8,
    int16,
    int32,
    int64,
    uint8,
    uint16,
    uint32,
    uint64,
)
_numeric_dtypes = (
    float32,
    float64,
    complex64,
    complex128,
    int8,
    int16,
    int32,
    int64,
    uint8,
    uint16,
    uint32,
    uint64,
)

_dtype_categories = {
    "all": _all_dtypes,
    "real numeric": _real_numeric_dtypes,
    "numeric": _numeric_dtypes,
    "integer": _integer_dtypes,
    "integer or boolean": _integer_or_boolean_dtypes,
    "boolean": _boolean_dtypes,
    "real floating-point": _floating_dtypes,
    "complex floating-point": _complex_floating_dtypes,
    "floating-point": _floating_dtypes,
}

# %% Constants
e = np.e
inf = np.inf
nan = np.nan
newaxis = np.newaxis
pi = np.pi


# %% Creation Functions
def astype(
    x: NamedArray[_ShapeType, Any], dtype: _DType, /, *, copy: bool = True
) -> NamedArray[_ShapeType, _DType]:
    """
    Copies an array to a specified data type irrespective of Type Promotion Rules rules.

    Parameters
    ----------
    x : NamedArray
        Array to cast.
    dtype : _DType
        Desired data type.
    copy : bool, optional
        Specifies whether to copy an array when the specified dtype matches the data
        type of the input array x.
        If True, a newly allocated array must always be returned.
        If False and the specified dtype matches the data type of the input array,
        the input array must be returned; otherwise, a newly allocated array must be
        returned. Default: True.

    Returns
    -------
    out : NamedArray
        An array having the specified data type. The returned array must have the
        same shape as x.

    Examples
    --------
    >>> narr = NamedArray(("x",), np.asarray([1.5, 2.5]))
    >>> narr
    <xarray.NamedArray (x: 2)> Size: 16B
    array([1.5, 2.5])
    >>> astype(narr, np.dtype(np.int32))
    <xarray.NamedArray (x: 2)> Size: 8B
    array([1, 2], dtype=int32)
    """
    if isinstance(x._data, _arrayapi):
        xp = x._data.__array_namespace__()
        return x._new(data=xp.astype(x._data, dtype, copy=copy))

    # np.astype doesn't exist yet:
    return x._new(data=x._data.astype(dtype, copy=copy))  # type: ignore[attr-defined]


# %% Elementwise Functions


def imag(
    x: NamedArray[_ShapeType, np.dtype[_SupportsImag[_ScalarType]]], /  # type: ignore[type-var]
) -> NamedArray[_ShapeType, np.dtype[_ScalarType]]:
    """
    Returns the imaginary component of a complex number for each element x_i of the
    input array x.

    Parameters
    ----------
    x : NamedArray
        Input array. Should have a complex floating-point data type.

    Returns
    -------
    out : NamedArray
        An array containing the element-wise results. The returned array must have a
        floating-point data type with the same floating-point precision as x
        (e.g., if x is complex64, the returned array must have the floating-point
        data type float32).

    Examples
    --------
    >>> narr = NamedArray(("x",), np.asarray([1.0 + 2j, 2 + 4j]))
    >>> imag(narr)
    <xarray.NamedArray (x: 2)> Size: 16B
    array([2., 4.])
    """
    xp = _get_data_namespace(x)
    out = x._new(data=xp.imag(x._data))
    return out


def real(
    x: NamedArray[_ShapeType, np.dtype[_SupportsReal[_ScalarType]]], /  # type: ignore[type-var]
) -> NamedArray[_ShapeType, np.dtype[_ScalarType]]:
    """
    Returns the real component of a complex number for each element x_i of the
    input array x.

    Parameters
    ----------
    x : NamedArray
        Input array. Should have a complex floating-point data type.

    Returns
    -------
    out : NamedArray
        An array containing the element-wise results. The returned array must have a
        floating-point data type with the same floating-point precision as x
        (e.g., if x is complex64, the returned array must have the floating-point
        data type float32).

    Examples
    --------
    >>> narr = NamedArray(("x",), np.asarray([1.0 + 2j, 2 + 4j]))
    >>> real(narr)
    <xarray.NamedArray (x: 2)> Size: 16B
    array([1., 2.])
    """
    xp = _get_data_namespace(x)
    out = x._new(data=xp.real(x._data))
    return out


# %% Manipulation functions
def expand_dims(
    x: NamedArray[Any, _DType],
    /,
    *,
    dim: _Dim | Default = _default,
    axis: _Axis = 0,
) -> NamedArray[Any, _DType]:
    """
    Expands the shape of an array by inserting a new dimension of size one at the
    position specified by dims.

    Parameters
    ----------
    x :
        Array to expand.
    dim :
        Dimension name. New dimension will be stored in the axis position.
    axis :
        (Not recommended) Axis position (zero-based). Default is 0.

    Returns
    -------
        out :
            An expanded output array having the same data type as x.

    Examples
    --------
    >>> x = NamedArray(("x", "y"), np.asarray([[1.0, 2.0], [3.0, 4.0]]))
    >>> expand_dims(x)
    <xarray.NamedArray (dim_2: 1, x: 2, y: 2)> Size: 32B
    array([[[1., 2.],
            [3., 4.]]])
    >>> expand_dims(x, dim="z")
    <xarray.NamedArray (z: 1, x: 2, y: 2)> Size: 32B
    array([[[1., 2.],
            [3., 4.]]])
    """
    xp = _get_data_namespace(x)
    dims = x.dims
    if dim is _default:
        dim = f"dim_{len(dims)}"
    d = list(dims)
    d.insert(axis, dim)
    out = x._new(dims=tuple(d), data=xp.expand_dims(x._data, axis=axis))
    return out


def permute_dims(x: NamedArray[Any, _DType], axes: _Axes) -> NamedArray[Any, _DType]:
    """
    Permutes the dimensions of an array.

    Parameters
    ----------
    x :
        Array to permute.
    axes :
        Permutation of the dimensions of x.

    Returns
    -------
    out :
        An array with permuted dimensions. The returned array must have the same
        data type as x.

    """

    dims = x.dims
    new_dims = tuple(dims[i] for i in axes)
    if isinstance(x._data, _arrayapi):
        xp = _get_data_namespace(x)
        out = x._new(dims=new_dims, data=xp.permute_dims(x._data, axes))
    else:
        out = x._new(dims=new_dims, data=x._data.transpose(axes))  # type: ignore[attr-defined]
    return out


# %% Statistical Functions
def mean(
    x: NamedArray[Any, _DType],
    /,
    *,
    dims: _Dims | Default = _default,
    keepdims: bool = False,
    axis: _AxisLike | None = None,
) -> NamedArray[Any, _DType]:
    """
    Calculates the arithmetic mean of the input array x.

    Parameters
    ----------
    x :
        Should have a real-valued floating-point data type.
    dims :
        Dim or dims along which arithmetic means must be computed. By default,
        the mean must be computed over the entire array. If a tuple of hashables,
        arithmetic means must be computed over multiple axes.
        Default: None.
    keepdims :
        if True, the reduced axes (dimensions) must be included in the result
        as singleton dimensions, and, accordingly, the result must be compatible
        with the input array (see Broadcasting). Otherwise, if False, the
        reduced axes (dimensions) must not be included in the result.
        Default: False.
    axis :
        Axis or axes along which arithmetic means must be computed. By default,
        the mean must be computed over the entire array. If a tuple of integers,
        arithmetic means must be computed over multiple axes.
        Default: None.

    Returns
    -------
    out :
        If the arithmetic mean was computed over the entire array,
        a zero-dimensional array containing the arithmetic mean; otherwise,
        a non-zero-dimensional array containing the arithmetic means.
        The returned array must have the same data type as x.

    Examples
    --------
    >>> x = NamedArray(("x", "y"), nxp.asarray([[1.0, 2.0], [3.0, 4.0]]))
    >>> mean(x).data
    Array(2.5, dtype=float64)
    >>> mean(x, dims=("x",)).data
    Array([2., 3.], dtype=float64)

    Using keepdims:

    >>> mean(x, dims=("x",), keepdims=True)
    <xarray.NamedArray (x: 1, y: 2)>
    Array([[2., 3.]], dtype=float64)
    >>> mean(x, dims=("y",), keepdims=True)
    <xarray.NamedArray (x: 2, y: 1)>
    Array([[1.5],
           [3.5]], dtype=float64)
    """
    xp = _get_data_namespace(x)
    axis_ = _dims_to_axis(x, dims, axis)
    d = xp.mean(x._data, axis=axis_, keepdims=False)  # We fix keepdims later
    # TODO: Why do we need to do the keepdims ourselves?
    dims_, data_ = _get_remaining_dims(x, d, axis_, keepdims=keepdims)
    out = x._new(dims=dims_, data=data_)
    return out
