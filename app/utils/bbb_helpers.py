import hashlib
import xml.etree.ElementTree as ET
from typing import Any

from fastapi import HTTPException


def generate_checksum(call_name: str, query_params: str, shared_secret: str) -> str:
    """Generates the checksum required for BBB API calls."""
    checksum_string = call_name + query_params + shared_secret
    return hashlib.sha1(checksum_string.encode("utf-8")).hexdigest()


def parse_xml_response(xml_content: bytes, api_call: str) -> dict[str, Any]:
    """Parses the XML response from BBB API."""
    try:
        root = ET.fromstring(xml_content)
        result: dict[str, Any] = {"returncode": root.findtext("returncode")}

        if result["returncode"] == "SUCCESS":
            # Process all child elements
            for child in root:
                if child.tag == "returncode":
                    continue

                # Handle complex nested structures (meetings, recordings, etc.)
                if len(child) > 0:  # Check if this element has children
                    # Handle collection elements like 'meetings', 'recordings'
                    if all(item.tag == child.tag[:-1] for item in child):  # Check if children follow naming pattern
                        collection = []
                        for item in child:
                            item_dict: dict[str, Any] = {}
                            _extract_element_data(item, item_dict)
                            collection.append(item_dict)
                        result[child.tag] = collection
                    else:
                        # For other nested structures
                        nested_dict: dict[str, Any] = {}
                        _extract_element_data(child, nested_dict)
                        result[child.tag] = nested_dict
                else:
                    # Simple elements
                    result[child.tag] = child.text
        else:
            # Extract error messages and messageKey
            result["message"] = root.findtext("message", "Unknown error")
            result["messageKey"] = root.findtext("messageKey", "")

        return result
    except ET.ParseError:
        raise HTTPException(status_code=500, detail="Failed to parse BBB response")


def _extract_element_data(element: ET.Element, target_dict: dict[str, Any]) -> None:
    """Helper function to recursively extract data from XML elements."""
    for child in element:
        # Handle complex nested elements (like playback, metadata)
        if len(child) > 0:
            # Special case for collections like 'formats' in playback
            if all(item.tag == child.tag[:-1] for item in child) and len(child) > 0:
                collection = []
                for item in child:
                    item_dict: dict[str, Any] = {}
                    _extract_element_data(item, item_dict)
                    collection.append(item_dict)
                target_dict[child.tag] = collection
            else:
                nested_dict: dict[str, Any] = {}
                _extract_element_data(child, nested_dict)
                target_dict[child.tag] = nested_dict
        else:
            # For simple elements, just extract the text
            target_dict[child.tag] = child.text
