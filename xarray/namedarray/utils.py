from __future__ import annotations

import importlib
import sys
import typing
from enum import Enum

import numpy as np

if typing.TYPE_CHECKING:
    if sys.version_info >= (3, 10):
        from typing import TypeGuard
    else:
        from typing_extensions import TypeGuard

    if sys.version_info >= (3, 11):
        from typing import Self
    else:
        from typing_extensions import Self

    try:
        from dask.array import Array as DaskArray
        from dask.types import DaskCollection
    except ImportError:
        DaskArray = np.ndarray  # type: ignore
        DaskCollection: typing.Any = np.ndarray  # type: ignore


# https://stackoverflow.com/questions/74633074/how-to-type-hint-a-generic-numpy-array
T_DType_co = typing.TypeVar("T_DType_co", bound=np.dtype[np.generic], covariant=True)
# T_DType = typing.TypeVar("T_DType", bound=np.dtype[np.generic])


class _Array(typing.Protocol[T_DType_co]):
    @property
    def dtype(self) -> T_DType_co:
        ...

    @property
    def shape(self) -> tuple[int, ...]:
        ...

    @property
    def real(self) -> Self:
        ...

    @property
    def imag(self) -> Self:
        ...

    def astype(self, dtype: np.typing.DTypeLike) -> Self:
        ...

    # def __array__(
    #     self, dtype: np.typing.DTypeLike = None
    # ) -> np.ndarray[typing.Any, np.dtype[np.generic]]:
    #     ...


class _ChunkedArray(_Array):
    def chunks(self) -> tuple[tuple[int, ...], ...]:
        ...


# temporary placeholder for indicating an array api compliant type.
# hopefully in the future we can narrow this down more
T_DuckArray = typing.TypeVar("T_DuckArray", bound=_Array[np.dtype[np.generic]])
T_ChunkedArray = typing.TypeVar(
    "T_ChunkedArray", bound=_ChunkedArray[np.dtype[np.generic]]
)


# Singleton type, as per https://github.com/python/typing/pull/240
class Default(Enum):
    token: typing.Final = 0


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


def is_dask_collection(x: typing.Any) -> TypeGuard[DaskCollection]:
    if module_available("dask"):
        from dask.typing import DaskCollection

        return isinstance(x, DaskCollection)
    return False


def is_duck_array(value: typing.Any) -> TypeGuard[T_DuckArray]:
    if isinstance(value, np.ndarray):
        return True
    return (
        hasattr(value, "ndim")
        and hasattr(value, "shape")
        and hasattr(value, "dtype")
        and (
            (hasattr(value, "__array_function__") and hasattr(value, "__array_ufunc__"))
            or hasattr(value, "__array_namespace__")
        )
    )


def is_duck_dask_array(x: typing.Any) -> TypeGuard[DaskArray]:
    return is_duck_array(x) and is_dask_collection(x)


def is_chunked_duck_array(x: T_DuckArray) -> TypeGuard[T_ChunkedArray]:
    return hasattr(x, "chunks")


def to_0d_object_array(
    value: typing.Any,
) -> np.ndarray[typing.Any, np.dtype[np.object_]]:
    """Given a value, wrap it in a 0-D numpy.ndarray with dtype=object."""
    result = np.empty((), dtype=object)
    result[()] = value
    return result
