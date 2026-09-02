import abc
from typing import Dict, Any, Optional

class SchemaRegistryInterface(abc.ABC):
    @abc.abstractmethod
    def get_schema_for_type(self, document_type: str) -> Optional[Dict[str, Any]]:
        pass

class DeterministicSchemaRegistry(SchemaRegistryInterface):
    """
    MVP versioned extraction-schema registry.
    """
    
    def __init__(self):
        self._schemas = {
            "INVOICE": {
                "schema_name": "invoice_schema",
                "schema_version": "v1.0.0",
                "schema_reference": "urn:resolveai:schema:invoice:v1.0.0",
                "active": True
            },
            "PROOF_OF_DELIVERY": {
                "schema_name": "pod_schema",
                "schema_version": "v1.0.0",
                "schema_reference": "urn:resolveai:schema:pod:v1.0.0",
                "active": True
            },
            "COURIER_TRACKING": {
                "schema_name": "tracking_schema",
                "schema_version": "v1.0.0",
                "schema_reference": "urn:resolveai:schema:tracking:v1.0.0",
                "active": True
            }
        }
        
    def get_schema_for_type(self, document_type: str) -> Optional[Dict[str, Any]]:
        """
        Returns the active schema for the given document type, or None if unknown.
        """
        return self._schemas.get(document_type)

schema_registry = DeterministicSchemaRegistry()
