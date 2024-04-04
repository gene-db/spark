#
# Licensed to the Apache Software Foundation (ASF) under one or more
# contributor license agreements.  See the NOTICE file distributed with
# this work for additional information regarding copyright ownership.
# The ASF licenses this file to You under the Apache License, Version 2.0
# (the "License"); you may not use this file except in compliance with
# the License.  You may obtain a copy of the License at
#
#    http://www.apache.org/licenses/LICENSE-2.0
#
# Unless required by applicable law or agreed to in writing, software
# distributed under the License is distributed on an "AS IS" BASIS,
# WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
# See the License for the specific language governing permissions and
# limitations under the License.
#

import decimal
import json
import struct
from array import array
from typing import Any


class VariantUtils:
    """
    A utility class for VariantVal.

    Adapted from library at: org.apache.spark.types.variant.VariantUtil
    """

    BASIC_TYPE_BITS = 2
    BASIC_TYPE_MASK = 0x3
    TYPE_INFO_MASK = 0x3F
    # The inclusive maximum value of the type info value. It is the size limit of `SHORT_STR`.
    MAX_SHORT_STR_SIZE = 0x3F

    # Below is all possible basic type values.
    # Primitive value. The type info value must be one of the values in the below section.
    PRIMITIVE = 0
    # Short string value. The type info value is the string size, which must be in `[0,
    # MAX_SHORT_STR_SIZE]`.
    # The string content bytes directly follow the header byte.
    SHORT_STR = 1
    # Object value. The content contains a size, a list of field ids, a list of field offsets, and
    # the actual field data. The length of the id list is `size`, while the length of the offset
    # list is `size + 1`, where the last offset represent the total size of the field data. The
    # fields in an object must be sorted by the field name in alphabetical order. Duplicate field
    # names in one object are not allowed.
    # We use 5 bits in the type info to specify the integer type of the object header: it should
    # be 0_b4_b3b2_b1b0 (MSB is 0), where:
    # - b4 specifies the type of size. When it is 0/1, `size` is a little-endian 1/4-byte
    # unsigned integer.
    # - b3b2/b1b0 specifies the integer type of id and offset. When the 2 bits are  0/1/2, the
    # list contains 1/2/3-byte little-endian unsigned integers.
    OBJECT = 2
    # Array value. The content contains a size, a list of field offsets, and the actual element
    # data. It is similar to an object without the id list. The length of the offset list
    # is `size + 1`, where the last offset represent the total size of the element data.
    # Its type info should be: 000_b2_b1b0:
    # - b2 specifies the type of size.
    # - b1b0 specifies the integer type of offset.
    ARRAY = 3

    # Below is all possible type info values for `PRIMITIVE`.
    # JSON Null value. Empty content.
    NULL = 0
    # True value. Empty content.
    TRUE = 1
    # False value. Empty content.
    FALSE = 2
    # 1-byte little-endian signed integer.
    INT1 = 3
    # 2-byte little-endian signed integer.
    INT2 = 4
    # 4-byte little-endian signed integer.
    INT4 = 5
    # 4-byte little-endian signed integer.
    INT8 = 6
    # 8-byte IEEE double.
    DOUBLE = 7
    # 4-byte decimal. Content is 1-byte scale + 4-byte little-endian signed integer.
    DECIMAL4 = 8
    # 8-byte decimal. Content is 1-byte scale + 8-byte little-endian signed integer.
    DECIMAL8 = 9
    # 16-byte decimal. Content is 1-byte scale + 16-byte little-endian signed integer.
    DECIMAL16 = 10
    # Long string value. The content is (4-byte little-endian unsigned integer representing the
    # string size) + (size bytes of string content).
    LONG_STR = 16

    U32_SIZE = 4

    @classmethod
    def to_json(cls, value: bytes, metadata: bytes) -> str:
        """
        Convert the VariantVal to a JSON string.
        :return: JSON string
        """
        return cls._to_json(value, metadata, 0)

    @classmethod
    def to_python(cls, value: bytes, metadata: bytes) -> str:
        """
        Convert the VariantVal to a nested Python object of Python data types.
        :return: Python representation of the Variant nested structure
        """
        return cls._to_python(value, metadata, 0)

    @classmethod
    def _read_long(cls, data: bytes, pos: int, num_bytes: int, signed: bool) -> int:
        cls._check_index(pos, len(data))
        cls._check_index(pos + num_bytes - 1, len(data))
        return int.from_bytes(data[pos : pos + num_bytes], byteorder="little", signed=signed)

    @classmethod
    def _check_index(cls, pos: int, length: int) -> None:
        if pos < 0 or pos >= length:
            raise Exception("Malformed Variant")

    @classmethod
    def _get_type_info(cls, value: bytes, pos: int):
        """
        Returns the (basic_type, type_info) pair from the given position in the value.
        """
        basic_type = value[pos] & VariantUtils.BASIC_TYPE_MASK
        type_info = (value[pos] >> VariantUtils.BASIC_TYPE_BITS) & VariantUtils.TYPE_INFO_MASK
        return (basic_type, type_info)

    @classmethod
    def _get_metadata_key(cls, metadata: bytes, id: int) -> str:
        """
        Returns the key string from the dictionary in the metadata, corresponding to `id`.
        """
        cls._check_index(0, len(metadata))
        offset_size = ((metadata[0] >> 6) & 0x3) + 1
        dict_size = cls._read_long(metadata, 1, offset_size, signed=False)
        if id >= dict_size:
            raise Exception("Malformed Variant")
        string_start = 1 + (dict_size + 2) * offset_size
        offset = cls._read_long(metadata, 1 + (id + 1) * offset_size, offset_size, signed=False)
        next_offset = cls._read_long(
            metadata, 1 + (id + 2) * offset_size, offset_size, signed=False
        )
        if offset > next_offset:
            raise Exception("Malformed Variant")
        cls._check_index(string_start + next_offset - 1, len(metadata))
        return metadata[string_start + offset : (string_start + next_offset)].decode("utf-8")

    @classmethod
    def _get_boolean(cls, value: bytes, pos: int) -> bool:
        cls._check_index(pos, len(value))
        basic_type, type_info = cls._get_type_info(value, pos)
        if basic_type != VariantUtils.PRIMITIVE or (
            type_info != VariantUtils.TRUE and type_info != VariantUtils.FALSE
        ):
            raise Exception("Unexpected Variant type: %s" % (str(bool)))
        return type_info == VariantUtils.TRUE

    @classmethod
    def _get_long(cls, value: bytes, pos: int) -> int:
        cls._check_index(pos, len(value))
        basic_type, type_info = cls._get_type_info(value, pos)
        if basic_type != VariantUtils.PRIMITIVE:
            raise Exception("Unexpected Variant type: %s" % (str(int)))
        if type_info == VariantUtils.INT1:
            return cls._read_long(value, pos + 1, 1, signed=True)
        elif type_info == VariantUtils.INT2:
            return cls._read_long(value, pos + 1, 2, signed=True)
        elif type_info == VariantUtils.INT4:
            return cls._read_long(value, pos + 1, 4, signed=True)
        elif type_info == VariantUtils.INT8:
            return cls._read_long(value, pos + 1, 8, signed=True)
        raise Exception("Unexpected Variant type: %s" % (str(int)))

    @classmethod
    def _get_string(cls, value: bytes, pos: int) -> str:
        cls._check_index(pos, len(value))
        basic_type, type_info = cls._get_type_info(value, pos)
        if basic_type == VariantUtils.SHORT_STR or (
            basic_type == VariantUtils.PRIMITIVE and type_info == VariantUtils.LONG_STR
        ):
            start = 0
            length = 0
            if basic_type == VariantUtils.SHORT_STR:
                start = pos + 1
                length = type_info
            else:
                start = pos + 1 + VariantUtils.U32_SIZE
                length = cls._read_long(value, pos + 1, VariantUtils.U32_SIZE, signed=False)
            cls._check_index(start + length - 1, len(value))
            return value[start : start + length].decode("utf-8")
        raise Exception("Unexpected Variant type: %s" % (str(str)))

    @classmethod
    def _get_double(cls, value: bytes, pos: int) -> float:
        cls._check_index(pos, len(value))
        basic_type, type_info = cls._get_type_info(value, pos)
        if basic_type != VariantUtils.PRIMITIVE or type_info != VariantUtils.DOUBLE:
            raise Exception("Unexpected Variant type: %s" % (str(float)))
        return struct.unpack("d", value[pos + 1 : pos + 9])[0]

    @classmethod
    def _get_decimal(cls, value: bytes, pos: int) -> decimal.Decimal:
        cls._check_index(pos, len(value))
        basic_type, type_info = cls._get_type_info(value, pos)
        if basic_type != VariantUtils.PRIMITIVE:
            raise Exception("Unexpected Variant type: %s" % (str(Decimal)))
        scale = value[pos + 1]
        unscaled = 0
        if type_info == VariantUtils.DECIMAL4:
            unscaled = cls._read_long(value, pos + 2, 4, signed=True)
        elif type_info == VariantUtils.DECIMAL8:
            unscaled = cls._read_long(value, pos + 2, 8, signed=True)
        elif type_info == VariantUtils.DECIMAL16:
            cls._check_index(pos + 17, len(value))
            unscaled = int.from_bytes(value[pos + 2 : pos + 18], byteorder="little", signed=True)
        else:
            raise Exception("Unexpected Variant type: %s" % (str(Decimal)))
        return decimal.Decimal(unscaled) * (decimal.Decimal(10) ** (-scale))

    @classmethod
    def _get_type(cls, value: bytes, pos: int):
        """
        Returns the Python type of the Variant at the given position.
        """
        cls._check_index(pos, len(value))
        basic_type, type_info = cls._get_type_info(value, pos)
        if basic_type == VariantUtils.SHORT_STR:
            return str
        elif basic_type == VariantUtils.OBJECT:
            return dict
        elif basic_type == VariantUtils.ARRAY:
            return array
        elif type_info == VariantUtils.NULL:
            return type(None)
        elif type_info == VariantUtils.TRUE or type_info == VariantUtils.FALSE:
            return bool
        elif (
            type_info == VariantUtils.INT1
            or type_info == VariantUtils.INT2
            or type_info == VariantUtils.INT4
            or type_info == VariantUtils.INT8
        ):
            return int
        elif type_info == VariantUtils.DOUBLE:
            return float
        elif (
            type_info == VariantUtils.DECIMAL4
            or type_info == VariantUtils.DECIMAL8
            or type_info == VariantUtils.DECIMAL16
        ):
            return decimal.Decimal
        elif type_info == VariantUtils.LONG_STR:
            return str
        raise Exception("Malformed Variant")

    @classmethod
    def _to_json(cls, value: bytes, metadata: bytes, pos: int) -> Any:
        variant_type = cls._get_type(value, pos)
        if variant_type == dict:

            def handle_object(key_value_pos_list):
                key_value_list = [
                    json.dumps(key) + ":" + cls._to_json(value, metadata, value_pos)
                    for (key, value_pos) in key_value_pos_list
                ]
                return "{" + ",".join(key_value_list) + "}"

            return cls._handle_object(value, metadata, pos, handle_object)
        elif variant_type == array:

            def handle_array(value_pos_list):
                value_list = [
                    cls._to_json(value, metadata, value_pos) for value_pos in value_pos_list
                ]
                return "[" + ",".join(value_list) + "]"

            return cls._handle_array(value, pos, handle_array)
        else:
            value = cls._get_scalar(variant_type, value, metadata, pos)
            if value == None:
                return "null"
            if type(value) == bool:
                return "true" if value else "false"
            if type(value) == str:
                return json.dumps(value)
            return str(value)

    @classmethod
    def _to_python(cls, value: bytes, metadata: bytes, pos: int) -> Any:
        variant_type = cls._get_type(value, pos)
        if variant_type == dict:

            def handle_object(key_value_pos_list):
                key_value_list = [
                    (key, cls._to_python(value, metadata, value_pos))
                    for (key, value_pos) in key_value_pos_list
                ]
                return dict(key_value_list)

            return cls._handle_object(value, metadata, pos, handle_object)
        elif variant_type == array:

            def handle_array(value_pos_list):
                value_list = [
                    cls._to_python(value, metadata, value_pos) for value_pos in value_pos_list
                ]
                return value_list

            return cls._handle_array(value, pos, handle_array)
        else:
            return cls._get_scalar(variant_type, value, metadata, pos)

    @classmethod
    def _get_scalar(cls, variant_type, value: bytes, metadata: bytes, pos: int) -> Any:
        if variant_type == type(None):
            return None
        elif variant_type == bool:
            return cls._get_boolean(value, pos)
        elif variant_type == int:
            return cls._get_long(value, pos)
        elif variant_type == str:
            return cls._get_string(value, pos)
        elif variant_type == float:
            return cls._get_double(value, pos)
        elif variant_type == decimal.Decimal:
            return cls._get_decimal(value, pos)
        else:
            raise Exception("Malformed Variant")

    @classmethod
    def _handle_object(cls, value: bytes, metadata: bytes, pos: int, func):
        """
        Parses the variant object at position `pos`.
        Calls `func` with a list of (key, value position) pairs of the object.
        """
        cls._check_index(pos, len(value))
        basic_type, type_info = cls._get_type_info(value, pos)
        if basic_type != VariantUtils.OBJECT:
            raise Exception("Malformed Variant")
        large_size = ((type_info >> 4) & 0x1) != 0
        size_bytes = VariantUtils.U32_SIZE if large_size else 1
        num_fields = cls._read_long(value, pos + 1, size_bytes, signed=False)
        id_size = ((type_info >> 2) & 0x3) + 1
        offset_size = ((type_info) & 0x3) + 1
        id_start = pos + 1 + size_bytes
        offset_start = id_start + num_fields * id_size
        data_start = offset_start + (num_fields + 1) * offset_size

        key_value_pos_list = []
        for i in range(num_fields):
            id = cls._read_long(value, id_start + id_size * i, id_size, signed=False)
            offset = cls._read_long(
                value, offset_start + offset_size * i, offset_size, signed=False
            )
            value_pos = data_start + offset
            key_value_pos_list.append((cls._get_metadata_key(metadata, id), value_pos))
        return func(key_value_pos_list)

    @classmethod
    def _handle_array(cls, value: bytes, pos: int, func):
        """
        Parses the variant array at position `pos`.
        Calls `func` with a list of element positions of the array.
        """
        cls._check_index(pos, len(value))
        basic_type, type_info = cls._get_type_info(value, pos)
        if basic_type != VariantUtils.ARRAY:
            raise Exception("Malformed Variant")
        large_size = ((type_info >> 2) & 0x1) != 0
        size_bytes = VariantUtils.U32_SIZE if large_size else 1
        num_fields = cls._read_long(value, pos + 1, size_bytes, signed=False)
        offset_size = (type_info & 0x3) + 1
        offset_start = pos + 1 + size_bytes
        data_start = offset_start + (num_fields + 1) * offset_size

        value_pos_list = []
        for i in range(num_fields):
            offset = cls._read_long(
                value, offset_start + offset_size * i, offset_size, signed=False
            )
            element_pos = data_start + offset
            value_pos_list.append(element_pos)
        return func(value_pos_list)
