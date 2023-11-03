from __future__ import annotations

import copy
import math
import sys
import warnings
from collections.abc import Hashable, Iterable, Mapping, Sequence
from typing import (
    TYPE_CHECKING,
    Any,
    Callable,
    Generic,
    Literal,
    TypeVar,
    cast,
    overload,
)

import numpy as np

# TODO: get rid of this after migrating this class to array API
from xarray.core import dtypes, formatting, formatting_html
from xarray.namedarray._aggregations import NamedArrayAggregations
from xarray.namedarray._typing import (
    _arrayapi,
    _arrayfunction_or_api,
    _chunkedarray,
    _default,
    _dtype,
    _DType_co,
    _ScalarType_co,
    _ShapeType_co,
    _sparsearrayfunction_or_api,
    _SupportsImag,
    _SupportsReal,
)
from xarray.namedarray.utils import is_duck_dask_array, to_0d_object_array

if TYPE_CHECKING:
    from numpy.typing import ArrayLike, NDArray

    from xarray.namedarray._typing import (
        Default,
        _AttrsLike,
        _Axes,
        _Axis,
        _AxisLike,
        _Chunks,
        _Dim,
        _Dims,
        _DimsLike,
        _DimsLikeAgg,
        _DType,
        _IntOrUnknown,
        _ScalarType,
        _Shape,
        _ShapeType,
        duckarray,
    )

    try:
        from dask.typing import (
            Graph,
            NestedKeys,
            PostComputeCallable,
            PostPersistCallable,
            SchedulerGetCallable,
        )
    except ImportError:
        Graph: Any  # type: ignore[no-redef]
        NestedKeys: Any  # type: ignore[no-redef]
        SchedulerGetCallable: Any  # type: ignore[no-redef]
        PostComputeCallable: Any  # type: ignore[no-redef]
        PostPersistCallable: Any  # type: ignore[no-redef]

    if sys.version_info >= (3, 11):
        from typing import Self
    else:
        from typing_extensions import Self

    T_NamedArray = TypeVar("T_NamedArray", bound="_NamedArray[Any]")
    T_NamedArrayInteger = TypeVar(
        "T_NamedArrayInteger", bound="_NamedArray[np.integer[Any]]"
    )


def _normalize_dimensions(dims: _DimsLike) -> _Dims:
    """
    Normalize dimensions.

    Examples
    --------
    >>> _normalize_dimensions(None)
    (None,)
    >>> _normalize_dimensions(1)
    (1,)
    >>> _normalize_dimensions("2")
    ('2',)
    >>> _normalize_dimensions(("time",))
    ('time',)
    >>> _normalize_dimensions(["time"])
    ('time',)
    >>> _normalize_dimensions([("time", "x", "y")])
    (('time', 'x', 'y'),)
    """
    if isinstance(dims, str) or not isinstance(dims, Iterable):
        return (dims,)

    return tuple(dims)


def _assert_either_dim_or_axis(
    dims: _Dim | _Dims | Default, axis: _AxisLike | None
) -> None:
    if dims is not _default and axis is not None:
        raise ValueError("cannot supply both 'axis' and 'dim(s)' arguments")


def _dims_to_axis(
    x: NamedArray[Any, Any], dims: _Dim | _Dims | Default, axis: _AxisLike | None
) -> _AxisLike | None:
    """
    Convert dims to axis indices.

    Examples
    --------
    >>> narr = NamedArray(("x", "y"), np.array([[1, 2, 3], [5, 6, 7]]))
    >>> _dims_to_axis(narr, ("y",), None)
    (1,)
    >>> _dims_to_axis(narr, None, 0)
    (0,)
    >>> _dims_to_axis(narr, None, None)
    """
    _assert_either_dim_or_axis(dims, axis)

    if dims is not _default:
        return x._dims_to_axes(dims)

    if isinstance(axis, int):
        return (axis,)

    return axis


def _get_remaining_dims(
    x: NamedArray[Any, _DType],
    data: duckarray[Any, _DType],
    axis: _AxisLike | None,
    *,
    keepdims: bool,
) -> tuple[_Dims, duckarray[Any, _DType]]:
    """
    Get the reamining dims after a reduce operation.

    Parameters
    ----------
    x :
        DESCRIPTION.
    data :
        DESCRIPTION.
    axis :
        DESCRIPTION.
    keepdims :
        DESCRIPTION.

    Returns
    -------
    tuple[_Dims, duckarray[Any, _DType]]
        DESCRIPTION.

    """
    if data.shape == x.shape:
        return x.dims, data

    removed_axes: np.ndarray[Any, np.dtype[np.intp]]
    if axis is None:
        removed_axes = np.arange(x.ndim, dtype=np.intp)
    else:
        removed_axes = np.atleast_1d(axis) % x.ndim

    if keepdims:
        # Insert np.newaxis for removed dims
        slices = tuple(
            np.newaxis if i in removed_axes else slice(None, None)
            for i in range(x.ndim)
        )
        data = data[slices]
        dims = x.dims
    else:
        dims = tuple(adim for n, adim in enumerate(x.dims) if n not in removed_axes)

    return dims, data


@overload
def _new(
    x: NamedArray[Any, _DType_co],
    dims: _DimsLike | Default = ...,
    data: duckarray[_ShapeType, _DType] = ...,
    attrs: _AttrsLike | Default = ...,
) -> NamedArray[_ShapeType, _DType]:
    ...


@overload
def _new(
    x: NamedArray[_ShapeType_co, _DType_co],
    dims: _DimsLike | Default = ...,
    data: Default = ...,
    attrs: _AttrsLike | Default = ...,
) -> NamedArray[_ShapeType_co, _DType_co]:
    ...


def _new(
    x: NamedArray[Any, _DType_co],
    dims: _DimsLike | Default = _default,
    data: duckarray[_ShapeType, _DType] | Default = _default,
    attrs: _AttrsLike | Default = _default,
) -> NamedArray[_ShapeType, _DType] | NamedArray[Any, _DType_co]:
    """
    Create a new array with new typing information.

    Parameters
    ----------
    x : NamedArray
        Array to create a new array from
    dims : Iterable of Hashable, optional
        Name(s) of the dimension(s).
        Will copy the dims from x by default.
    data : duckarray, optional
        The actual data that populates the array. Should match the
        shape specified by `dims`.
        Will copy the data from x by default.
    attrs : dict, optional
        A dictionary containing any additional information or
        attributes you want to store with the array.
        Will copy the attrs from x by default.
    """
    dims_ = copy.copy(x._dims) if dims is _default else dims

    attrs_: Mapping[Any, Any] | None
    if attrs is _default:
        attrs_ = None if x._attrs is None else x._attrs.copy()
    else:
        attrs_ = attrs

    if data is _default:
        return type(x)(dims_, copy.copy(x._data), attrs_)
    else:
        cls_ = cast("type[NamedArray[_ShapeType, _DType]]", type(x))
        return cls_(dims_, data, attrs_)


@overload
def from_array(
    dims: _DimsLike,
    data: duckarray[_ShapeType, _DType],
    attrs: _AttrsLike = ...,
) -> NamedArray[_ShapeType, _DType]:
    ...


@overload
def from_array(
    dims: _DimsLike,
    data: ArrayLike,
    attrs: _AttrsLike = ...,
) -> NamedArray[Any, Any]:
    ...


def from_array(
    dims: _DimsLike,
    data: duckarray[_ShapeType, _DType] | ArrayLike,
    attrs: _AttrsLike = None,
) -> NamedArray[_ShapeType, _DType] | NamedArray[Any, Any]:
    """
    Create a Named array from an array-like object.

    Parameters
    ----------
    dims : str or iterable of str
        Name(s) of the dimension(s).
    data : T_DuckArray or ArrayLike
        The actual data that populates the array. Should match the
        shape specified by `dims`.
    attrs : dict, optional
        A dictionary containing any additional information or
        attributes you want to store with the array.
        Default is None, meaning no attributes will be stored.
    """
    if isinstance(data, NamedArray):
        raise TypeError(
            "Array is already a Named array. Use 'data.data' to retrieve the data array"
        )

    # TODO: dask.array.ma.MaskedArray also exists, better way?
    if isinstance(data, np.ma.MaskedArray):
        mask = np.ma.getmaskarray(data)  # type: ignore[no-untyped-call]
        if mask.any():
            # TODO: requires refactoring/vendoring xarray.core.dtypes and
            # xarray.core.duck_array_ops
            raise NotImplementedError("MaskedArray is not supported yet")

        return NamedArray(dims, data_masked, attrs)

    if isinstance(data, _arrayfunction_or_api):
        return NamedArray(dims, data, attrs)

    if isinstance(data, tuple):
        return NamedArray(dims, to_0d_object_array(data), attrs)

    # validate whether the data is valid data types.
    return NamedArray(dims, np.asarray(data), attrs)


class NamedArray(NamedArrayAggregations, Generic[_ShapeType_co, _DType_co]):
    """
    A wrapper around duck arrays with named dimensions
    and attributes which describe a single Array.
    Numeric operations on this object implement array broadcasting and
    dimension alignment based on dimension names,
    rather than axis order.


    Parameters
    ----------
    dims : str or iterable of hashable
        Name(s) of the dimension(s).
    data : array-like or duck-array
        The actual data that populates the array. Should match the
        shape specified by `dims`.
    attrs : dict, optional
        A dictionary containing any additional information or
        attributes you want to store with the array.
        Default is None, meaning no attributes will be stored.

    Raises
    ------
    ValueError
        If the `dims` length does not match the number of data dimensions (ndim).


    Examples
    --------
    >>> data = np.array([1.5, 2, 3], dtype=float)
    >>> narr = NamedArray(("x",), data, {"units": "m"})  # TODO: Better name than narr?
    """

    __slots__ = ("_data", "_dims", "_attrs")

    _data: duckarray[Any, _DType_co]
    _dims: _Dims
    _attrs: dict[Any, Any] | None

    def __init__(
        self,
        dims: _DimsLike,
        data: duckarray[Any, _DType_co],
        attrs: _AttrsLike = None,
    ):
        self._data = data
        self._dims = self._parse_dimensions(dims)
        self._attrs = dict(attrs) if attrs else None

    def __init_subclass__(cls, **kwargs: Any) -> None:
        if NamedArray in cls.__bases__ and (cls._new == NamedArray._new):
            # Type hinting does not work for subclasses unless _new is
            # overridden with the correct class.
            raise TypeError(
                "Subclasses of `NamedArray` must override the `_new` method."
            )
        super().__init_subclass__(**kwargs)

    @overload
    def _new(
        self,
        dims: _DimsLike | Default = ...,
        data: duckarray[_ShapeType, _DType] = ...,
        attrs: _AttrsLike | Default = ...,
    ) -> NamedArray[_ShapeType, _DType]:
        ...

    @overload
    def _new(
        self,
        dims: _DimsLike | Default = ...,
        data: Default = ...,
        attrs: _AttrsLike | Default = ...,
    ) -> NamedArray[_ShapeType_co, _DType_co]:
        ...

    def _new(
        self,
        dims: _DimsLike | Default = _default,
        data: duckarray[Any, _DType] | Default = _default,
        attrs: _AttrsLike | Default = _default,
    ) -> NamedArray[_ShapeType, _DType] | NamedArray[_ShapeType_co, _DType_co]:
        """
        Create a new array with new typing information.

        _new has to be reimplemented each time NamedArray is subclassed,
        otherwise type hints will not be correct. The same is likely true
        for methods that relied on _new.

        Parameters
        ----------
        dims : Iterable of Hashable, optional
            Name(s) of the dimension(s).
            Will copy the dims from x by default.
        data : duckarray, optional
            The actual data that populates the array. Should match the
            shape specified by `dims`.
            Will copy the data from x by default.
        attrs : dict, optional
            A dictionary containing any additional information or
            attributes you want to store with the array.
            Will copy the attrs from x by default.
        """
        return _new(self, dims, data, attrs)

    def _replace(
        self,
        dims: _DimsLike | Default = _default,
        data: duckarray[_ShapeType_co, _DType_co] | Default = _default,
        attrs: _AttrsLike | Default = _default,
    ) -> Self:
        """
        Create a new array with the same typing information.

        The types for each argument cannot change,
        use self._new if that is a risk.

        Parameters
        ----------
        dims : Iterable of Hashable, optional
            Name(s) of the dimension(s).
            Will copy the dims from x by default.
        data : duckarray, optional
            The actual data that populates the array. Should match the
            shape specified by `dims`.
            Will copy the data from x by default.
        attrs : dict, optional
            A dictionary containing any additional information or
            attributes you want to store with the array.
            Will copy the attrs from x by default.
        """
        return cast("Self", self._new(dims, data, attrs))

    def _copy(
        self,
        deep: bool = True,
        data: duckarray[_ShapeType_co, _DType_co] | None = None,
        memo: dict[int, Any] | None = None,
    ) -> Self:
        if data is None:
            ndata = self._data
            if deep:
                ndata = copy.deepcopy(ndata, memo=memo)
        else:
            ndata = data
            self._check_shape(ndata)

        attrs = (
            copy.deepcopy(self._attrs, memo=memo) if deep else copy.copy(self._attrs)
        )

        return self._replace(data=ndata, attrs=attrs)

    def __copy__(self) -> Self:
        return self._copy(deep=False)

    def __deepcopy__(self, memo: dict[int, Any] | None = None) -> Self:
        return self._copy(deep=True, memo=memo)

    def copy(
        self,
        deep: bool = True,
        data: duckarray[_ShapeType_co, _DType_co] | None = None,
    ) -> Self:
        """Returns a copy of this object.

        If `deep=True`, the data array is loaded into memory and copied onto
        the new object. Dimensions, attributes and encodings are always copied.

        Use `data` to create a new object with the same structure as
        original but entirely new data.

        Parameters
        ----------
        deep : bool, default: True
            Whether the data array is loaded into memory and copied onto
            the new object. Default is True.
        data : array_like, optional
            Data to use in the new object. Must have same shape as original.
            When `data` is used, `deep` is ignored.

        Returns
        -------
        object : NamedArray
            New object with dimensions, attributes, and optionally
            data copied from original.


        """
        return self._copy(deep=deep, data=data)

    @property
    def ndim(self) -> int:
        """
        Number of array dimensions.

        See Also
        --------
        numpy.ndarray.ndim
        """
        return len(self.shape)

    @property
    def size(self) -> _IntOrUnknown:
        """
        Number of elements in the array.

        Equal to ``np.prod(a.shape)``, i.e., the product of the array’s dimensions.

        See Also
        --------
        numpy.ndarray.size
        """
        return math.prod(self.shape)

    def __len__(self) -> _IntOrUnknown:
        try:
            return self.shape[0]
        except Exception as exc:
            raise TypeError("len() of unsized object") from exc

    @property
    def dtype(self) -> _DType_co:
        """
        Data-type of the array’s elements.

        See Also
        --------
        ndarray.dtype
        numpy.dtype
        """
        return self._data.dtype

    @property
    def shape(self) -> _Shape:
        """
        Get the shape of the array.

        Returns
        -------
        shape : tuple of ints
            Tuple of array dimensions.

        See Also
        --------
        numpy.ndarray.shape
        """
        return self._data.shape

    @property
    def nbytes(self) -> _IntOrUnknown:
        """
        Total bytes consumed by the elements of the data array.

        If the underlying data array does not include ``nbytes``, estimates
        the bytes consumed based on the ``size`` and ``dtype``.
        """
        if hasattr(self._data, "nbytes"):
            return self._data.nbytes  # type: ignore[no-any-return]
        else:
            return self.size * self.dtype.itemsize

    @property
    def dims(self) -> _Dims:
        """Tuple of dimension names with which this NamedArray is associated."""
        return self._dims

    @dims.setter
    def dims(self, value: _DimsLike) -> None:
        self._dims = self._parse_dimensions(value)

    def _parse_dimensions(self, dims: _DimsLike) -> _Dims:
        dims = _normalize_dimensions(dims)
        if len(dims) != self.ndim:
            raise ValueError(
                f"dimensions {dims} must have the same length as the "
                f"number of data dimensions, ndim={self.ndim}"
            )
        return dims

    @property
    def attrs(self) -> dict[Any, Any]:
        """Dictionary of local attributes on this NamedArray."""
        if self._attrs is None:
            self._attrs = {}
        return self._attrs

    @attrs.setter
    def attrs(self, value: Mapping[Any, Any]) -> None:
        self._attrs = dict(value)

    def _check_shape(self, new_data: duckarray[Any, _DType_co]) -> None:
        if new_data.shape != self.shape:
            raise ValueError(
                f"replacement data must match the {self.__class__.__name__}'s shape. "
                f"replacement data has shape {new_data.shape}; {self.__class__.__name__} has shape {self.shape}"
            )

    @property
    def data(self) -> duckarray[Any, _DType_co]:
        """
        The NamedArray's data as an array. The underlying array type
        (e.g. dask, sparse, pint) is preserved.

        """

        return self._data

    @data.setter
    def data(self, data: duckarray[Any, _DType_co]) -> None:
        self._check_shape(data)
        self._data = data

    @property
    def imag(
        self: NamedArray[_ShapeType, np.dtype[_SupportsImag[_ScalarType]]],  # type: ignore[type-var]
    ) -> NamedArray[_ShapeType, _dtype[_ScalarType]]:
        """
        The imaginary part of the array.

        See Also
        --------
        numpy.ndarray.imag
        """
        if isinstance(self._data, _arrayapi):
            from xarray.namedarray._array_api import imag

            return imag(self)

        return self._new(data=self._data.imag)

    @property
    def real(
        self: NamedArray[_ShapeType, np.dtype[_SupportsReal[_ScalarType]]],  # type: ignore[type-var]
    ) -> NamedArray[_ShapeType, _dtype[_ScalarType]]:
        """
        The real part of the array.

        See Also
        --------
        numpy.ndarray.real
        """
        if isinstance(self._data, _arrayapi):
            from xarray.namedarray._array_api import real

            return real(self)
        return self._new(data=self._data.real)

    def __dask_tokenize__(self) -> Hashable:
        # Use v.data, instead of v._data, in order to cope with the wrappers
        # around NetCDF and the like
        from dask.base import normalize_token

        s, d, a, attrs = type(self), self._dims, self.data, self.attrs
        return normalize_token((s, d, a, attrs))  # type: ignore[no-any-return]

    def __dask_graph__(self) -> Graph | None:
        if is_duck_dask_array(self._data):
            return self._data.__dask_graph__()
        else:
            # TODO: Should this method just raise instead?
            # raise NotImplementedError("Method requires self.data to be a dask array")
            return None

    def __dask_keys__(self) -> NestedKeys:
        if is_duck_dask_array(self._data):
            return self._data.__dask_keys__()
        else:
            raise AttributeError("Method requires self.data to be a dask array.")

    def __dask_layers__(self) -> Sequence[str]:
        if is_duck_dask_array(self._data):
            return self._data.__dask_layers__()
        else:
            raise AttributeError("Method requires self.data to be a dask array.")

    @property
    def __dask_optimize__(
        self,
    ) -> Callable[..., dict[Any, Any]]:
        if is_duck_dask_array(self._data):
            return self._data.__dask_optimize__  # type: ignore[no-any-return]
        else:
            raise AttributeError("Method requires self.data to be a dask array.")

    @property
    def __dask_scheduler__(self) -> SchedulerGetCallable:
        if is_duck_dask_array(self._data):
            return self._data.__dask_scheduler__
        else:
            raise AttributeError("Method requires self.data to be a dask array.")

    def __dask_postcompute__(
        self,
    ) -> tuple[PostComputeCallable, tuple[Any, ...]]:
        if is_duck_dask_array(self._data):
            array_func, array_args = self._data.__dask_postcompute__()  # type: ignore[no-untyped-call]
            return self._dask_finalize, (array_func,) + array_args
        else:
            raise AttributeError("Method requires self.data to be a dask array.")

    def __dask_postpersist__(
        self,
    ) -> tuple[
        Callable[
            [Graph, PostPersistCallable[Any], Any, Any],
            Self,
        ],
        tuple[Any, ...],
    ]:
        if is_duck_dask_array(self._data):
            a: tuple[PostPersistCallable[Any], tuple[Any, ...]]
            a = self._data.__dask_postpersist__()  # type: ignore[no-untyped-call]
            array_func, array_args = a

            return self._dask_finalize, (array_func,) + array_args
        else:
            raise AttributeError("Method requires self.data to be a dask array.")

    def _dask_finalize(
        self,
        results: Graph,
        array_func: PostPersistCallable[Any],
        *args: Any,
        **kwargs: Any,
    ) -> Self:
        data = array_func(results, *args, **kwargs)
        return type(self)(self._dims, data, attrs=self._attrs)

    @overload
    def _dims_to_axes(self, dims: _Dims) -> _Axes:
        ...

    @overload
    def _dims_to_axes(self, dims: _Dim) -> _Axis:
        ...

    @overload
    def _dims_to_axes(self, dims: Default = _default) -> None:
        ...

    def _dims_to_axes(
        self, dims: _Dims | _Dim | Default = _default
    ) -> _Axes | _Axis | None:
        """Return axis number(s) corresponding to dimension(s) in this array.

        Parameters
        ----------
        dim : str or iterable of str
            Dimension name(s) for which to lookup axes.

        Returns
        -------
        int or tuple of int
            Axis number or numbers corresponding to the given dimensions.
        """
        if dims is _default:
            return None

        if isinstance(dims, tuple):
            return tuple(self._dim_to_axis(d) for d in dims)

        return self._dim_to_axis(dims)

    def _dim_to_axis(self, dim: _Dim) -> int:
        try:
            out = self.dims.index(dim)
            return out
        except ValueError:
            raise ValueError(f"{dim!r} not found in array dimensions {self.dims!r}")

    @property
    def chunks(self) -> _Chunks | None:
        """
        Tuple of block lengths for this NamedArray's data, in order of dimensions, or None if
        the underlying data is not a dask array.

        See Also
        --------
        NamedArray.chunk
        NamedArray.chunksizes
        xarray.unify_chunks
        """
        data = self._data
        if isinstance(data, _chunkedarray):
            return data.chunks
        else:
            return None

    @property
    def chunksizes(
        self,
    ) -> Mapping[_Dim, _Shape]:
        """
        Mapping from dimension names to block lengths for this namedArray's data, or None if
        the underlying data is not a dask array.
        Cannot be modified directly, but can be modified by calling .chunk().

        Differs from NamedArray.chunks because it returns a mapping of dimensions to chunk shapes
        instead of a tuple of chunk shapes.

        See Also
        --------
        NamedArray.chunk
        NamedArray.chunks
        xarray.unify_chunks
        """
        data = self._data
        if isinstance(data, _chunkedarray):
            return dict(zip(self.dims, data.chunks))
        else:
            return {}

    @property
    def sizes(self) -> dict[_Dim, _IntOrUnknown]:
        """Ordered mapping from dimension names to lengths."""
        return dict(zip(self.dims, self.shape))

    def reduce(
        self,
        func: Callable[..., Any],
        dim: _DimsLikeAgg | Default = _default,
        axis: int | Sequence[int] | None = None,  # TODO: Use _AxisLike
        keepdims: bool = False,
        **kwargs: Any,
    ) -> NamedArray[Any, Any]:
        """Reduce this array by applying `func` along some dimension(s).

        Parameters
        ----------
        func : callable
            Function which can be called in the form
            `func(x, axis=axis, **kwargs)` to return the result of reducing an
            np.ndarray over an integer valued axis.
        dim : "...", str, Iterable of Hashable or None, optional
            Dimension(s) over which to apply `func`. By default `func` is
            applied over all dimensions.
        axis : int or Sequence of int, optional
            Axis(es) over which to apply `func`. Only one of the 'dim'
            and 'axis' arguments can be supplied. If neither are supplied, then
            the reduction is calculated over the flattened array (by calling
            `func(x)` without an axis argument).
        keepdims : bool, default: False
            If True, the dimensions which are reduced are left in the result
            as dimensions of size one
        **kwargs : dict
            Additional keyword arguments passed on to `func`.

        Returns
        -------
        reduced : Array
            Array with summarized data and the indicated dimension(s)
            removed.
        """
        d: _Dims | None
        if dim is None or dim is ...:  # TODO: isinstance(dim, types.EllipsisType)
            # TODO: What's the point of ellipsis? Use either ... or None?
            d = None
        else:
            dimslike: _DimsLike = dim
            d = _normalize_dimensions(dimslike)

        axislike: _AxisLike | None
        if axis is None or isinstance(axis, int):
            axislike = axis
        else:
            axislike = tuple(axis)
        axis_ = _dims_to_axis(self, d, axislike)

        data: duckarray[Any, Any] | ArrayLike
        with warnings.catch_warnings():
            warnings.filterwarnings(
                "ignore", r"Mean of empty slice", category=RuntimeWarning
            )
            if axis_ is not None:
                if isinstance(axis_, tuple) and len(axis_) == 1:
                    # unpack axis for the benefit of functions
                    # like np.argmin which can't handle tuple arguments
                    data = func(self.data, axis=axis_[0], **kwargs)
                else:
                    data = func(self.data, axis=axis_, **kwargs)
            else:
                data = func(self.data, **kwargs)

        if not isinstance(data, _arrayfunction_or_api):
            data = np.asarray(data)

        dims_, data = _get_remaining_dims(self, data, axis_, keepdims=keepdims)

        # Return NamedArray to handle IndexVariable when data is nD
        return from_array(dims_, data, attrs=self._attrs)

    def _nonzero(self: T_NamedArrayInteger) -> tuple[T_NamedArrayInteger, ...]:
        """Equivalent numpy's nonzero but returns a tuple of NamedArrays."""
        # TODO: we should replace dask's native nonzero
        # after https://github.com/dask/dask/issues/1076 is implemented.
        # TODO: cast to ndarray and back to T_DuckArray is a workaround
        nonzeros = np.nonzero(cast("NDArray[np.integer[Any]]", self.data))
        _attrs = self.attrs
        return tuple(
            cast("T_NamedArrayInteger", self._new((dim,), nz, _attrs))
            for nz, dim in zip(nonzeros, self.dims)
        )

    def __repr__(self) -> str:
        return formatting.array_repr(self)

    def _repr_html_(self) -> str:
        return formatting_html.array_repr(self)

    def _as_sparse(
        self,
        sparse_format: Literal["coo"] | Default = _default,
        fill_value: ArrayLike | Default = _default,
    ) -> NamedArray[Any, _DType_co]:
        """
        Use sparse-array as backend.
        """
        import sparse

        from xarray.namedarray._array_api import astype

        # TODO: what to do if dask-backended?
        if fill_value is _default:
            dtype, fill_value = dtypes.maybe_promote(self.dtype)
        else:
            dtype = dtypes.result_type(self.dtype, fill_value)

        if sparse_format is _default:
            sparse_format = "coo"
        try:
            as_sparse = getattr(sparse, f"as_{sparse_format.lower()}")
        except AttributeError as exc:
            raise ValueError(f"{sparse_format} is not a valid sparse format") from exc

        data = as_sparse(astype(self, dtype).data, fill_value=fill_value)
        return self._new(data=data)

    def _to_dense(self) -> NamedArray[Any, _DType_co]:
        """
        Change backend from sparse to np.array.
        """
        if isinstance(self._data, _sparsearrayfunction_or_api):
            data_dense: np.ndarray[Any, _DType_co] = self._data.todense()
            return self._new(data=data_dense)
        else:
            raise TypeError("self.data is not a sparse array")


_NamedArray = NamedArray[Any, np.dtype[_ScalarType_co]]
