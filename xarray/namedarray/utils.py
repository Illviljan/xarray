from __future__ import annotations

import importlib
import sys
from collections.abc import Hashable
from enum import Enum
from typing import TYPE_CHECKING, Any, Final, Protocol, TypeVar, runtime_checkable

import numpy as np

if TYPE_CHECKING:
    if sys.version_info >= (3, 10):
        from typing import TypeGuard
    else:
        from typing_extensions import TypeGuard

    if sys.version_info >= (3, 11):
        from typing import Self
    else:
        from typing_extensions import Self

    from numpy.typing import DTypeLike, NDArray
    from dask.array.core import Array as DaskArray
    from dask.typing import DaskCollection

    # try:
    #     from dask.array.core import Array as DaskArray
    #     from dask.typing import DaskCollection
    # except ImportError:
    #     DaskArray = NDArray  # type: ignore
    #     DaskCollection: Any = NDArray  # type: ignore


# https://stackoverflow.com/questions/74633074/how-to-type-hint-a-generic-numpy-array
T_DType_co = TypeVar("T_DType_co", bound=np.dtype[np.generic], covariant=True)
# T_DType = TypeVar("T_DType", bound=np.dtype[np.generic])
_ScalarType_co = TypeVar("_ScalarType_co", bound=np.generic, covariant=True)


@runtime_checkable
class _Array(Protocol[_ScalarType_co]):
    @property
    def dtype(self) -> np.dtype[_ScalarType_co]:
        ...

    @property
    def shape(self) -> tuple[int, ...]:
        ...

    @property
    def real(self) -> Self:  # _Array[np.dtype[np.generic]]:
        ...

    @property
    def imag(self) -> Self:  # _Array[np.dtype[np.generic]]:
        ...

    def astype(self, dtype: DTypeLike) -> Self:
        ...

    # def to_numpy(self) -> NDArray[_ScalarType_co]:
    #     ...

    # # TODO: numpy doesn't use any inputs:
    # # https://github.com/numpy/numpy/blob/v1.24.3/numpy/_typing/_array_like.py#L38
    # def __array__(self) -> NDArray[_ScalarType_co]:
    #     ...


@runtime_checkable
class _ChunkedArray(_Array[_ScalarType_co], Protocol[_ScalarType_co]):
    @property
    def chunks(self) -> tuple[tuple[int, ...], ...]:
        ...


@runtime_checkable
class _SparseArray(_Array[_ScalarType_co], Protocol[_ScalarType_co]):
    def todense(self) -> NDArray[_ScalarType_co]:
        ...


# temporary placeholder for indicating an array api compliant type.
# hopefully in the future we can narrow this down more
T_DuckArray = TypeVar("T_DuckArray", bound=_Array[np.generic])
T_ChunkedArray = TypeVar("T_ChunkedArray", bound=_ChunkedArray[np.generic])


# Singleton type, as per https://github.com/python/typing/pull/240
class Default(Enum):
    token: Final = 0


_default = Default.token


def module_available(module: str) -> bool:
    """Checks whether a module is installed without importing it.

    Use this for a lightweight check and lazy imports.

    Parameters
    ----------
    module : str
        Name of the module.

    Returns
    -------
    available : bool
        Whether the module is installed.
    """
    return importlib.util.find_spec(module) is not None


def is_dask_collection(x: object) -> TypeGuard[DaskCollection]:
    if module_available("dask"):
        from dask.typing import DaskCollection

        return isinstance(x, DaskCollection)
    return False


_T = TypeVar("_T")


def is_duck_array(value: _T) -> TypeGuard[_T]:
    # if isinstance(value, np.ndarray):
    #     return True
    return isinstance(value, _Array) and (
        (hasattr(value, "__array_function__") and hasattr(value, "__array_ufunc__"))
        or hasattr(value, "__array_namespace__")
    )


def is_duck_dask_array(x: _Array[np.generic]) -> TypeGuard[DaskArray]:
    return is_dask_collection(x)


def is_chunked_duck_array(
    x: _Array[np.generic],
) -> TypeGuard[_ChunkedArray[np.generic]]:
    return hasattr(x, "chunks")


def to_0d_object_array(
    value: object,
) -> NDArray[np.object_]:
    """Given a value, wrap it in a 0-D numpy.ndarray with dtype=object."""
    result = np.empty((), dtype=object)
    result[()] = value
    return result


class ReprObject:
    """Object that prints as the given value, for use with sentinel values."""

    __slots__ = ("_value",)

    _value: str

    def __init__(self, value: str):
        self._value = value

    def __repr__(self) -> str:
        return self._value

    def __eq__(self, other: ReprObject | Any) -> bool:
        # TODO: What type can other be? ArrayLike?
        return self._value == other._value if isinstance(other, ReprObject) else False

    def __hash__(self) -> int:
        return hash((type(self), self._value))

    def __dask_tokenize__(self) -> Hashable:
        from dask.base import normalize_token

        return normalize_token((type(self), self._value))  # type: ignore[no-any-return]
