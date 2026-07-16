from astropy.coordinates import Angle
import astropy.units as u
from typing import cast

def sexagesimal_to_decimal(value: str, obj_type: str) -> float:
    match obj_type:
        case "ra":
            return cast(float, Angle(value, unit=u.hourangle).hour)
        case "dec":
            return cast(float, Angle(value, unit=u.deg).deg)
        case _:
            raise ValueError(f"Invalid object type: '{obj_type}'")