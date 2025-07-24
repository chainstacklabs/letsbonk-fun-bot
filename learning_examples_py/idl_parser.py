"""
IDL Parser module for Solana programs.
Provides functionality to load and parse Anchor IDL files and decode instruction data.
"""

import json
import struct
from typing import Dict, Any, Optional

import base58


class IDLParser:
    """Parser for automatically decoding instructions using IDL definitions."""
    
    def __init__(self, idl_path: str, verbose: bool = False):
        """
        Initialize the IDL parser.
        
        Args:
            idl_path: Path to the IDL JSON file
            verbose: Whether to print debug information during initialization
        """
        self.verbose = verbose
        with open(idl_path, 'r') as f:
            self.idl = json.load(f)
        self._build_instruction_map()
        self._build_type_map()
        self._calculate_instruction_sizes()
    
    def _build_instruction_map(self):
        """Build a map of discriminators to instruction definitions."""
        self.instructions = {}
        for instruction in self.idl.get('instructions', []):
            discriminator = bytes(instruction['discriminator'])
            self.instructions[discriminator] = instruction
    
    def _build_type_map(self):
        """Build a map of type names to their definitions."""
        self.types = {}
        for type_def in self.idl.get('types', []):
            self.types[type_def['name']] = type_def
    
    def _calculate_instruction_sizes(self):
        """Calculate minimum data sizes for each instruction."""
        self.instruction_min_sizes = {}
        for discriminator, instruction in self.instructions.items():
            try:
                min_size = 8  # discriminator size
                for arg in instruction.get('args', []):
                    min_size += self._calculate_type_min_size(arg['type'])
                self.instruction_min_sizes[discriminator] = min_size
                # Only show size for initialize instruction if verbose mode is on
                if self.verbose and instruction['name'] == 'initialize':
                    print(f"📏 Initialize instruction min size: {min_size} bytes")
            except Exception as e:
                if self.verbose:
                    print(f"⚠️  Could not calculate size for {instruction['name']}: {e}")
                self.instruction_min_sizes[discriminator] = 8  # Just discriminator
    
    def _calculate_type_min_size(self, type_def) -> int:
        """Calculate minimum size in bytes for a type definition."""
        if isinstance(type_def, str):
            return self._get_primitive_size(type_def)
        elif isinstance(type_def, dict):
            if 'defined' in type_def:
                # Handle both old and new IDL format
                if isinstance(type_def['defined'], dict):
                    return self._calculate_defined_type_min_size(type_def['defined']['name'])
                else:
                    return self._calculate_defined_type_min_size(type_def['defined'])
            else:
                raise ValueError(f"Unknown type definition: {type_def}")
        else:
            raise ValueError(f"Invalid type definition: {type_def}")
    
    def _get_primitive_size(self, type_name: str) -> int:
        """Get size in bytes for primitive types."""
        sizes = {
            'u8': 1,
            'u16': 2,
            'u32': 4,
            'u64': 8,
            'i8': 1,
            'i16': 2,
            'i32': 4,
            'i64': 8,
            'bool': 1,
            'pubkey': 32,
            'string': 4,  # minimum for length prefix, actual string can be longer
        }
        return sizes.get(type_name, 0)
    
    def _calculate_defined_type_min_size(self, type_name: str) -> int:
        """Calculate minimum size for user-defined types."""
        if type_name not in self.types:
            raise ValueError(f"Unknown type: {type_name}")
        
        type_def = self.types[type_name]
        
        if type_def['type']['kind'] == 'struct':
            total_size = 0
            for field in type_def['type']['fields']:
                total_size += self._calculate_type_min_size(field['type'])
            return total_size
        
        elif type_def['type']['kind'] == 'enum':
            # Enum has 1 byte discriminator + smallest variant size
            min_variant_size = float('inf')
            for variant in type_def['type']['variants']:
                variant_size = 0
                for field in variant.get('fields', []):
                    variant_size += self._calculate_type_min_size(field['type'])
                min_variant_size = min(min_variant_size, variant_size)
            
            return 1 + (0 if min_variant_size == float('inf') else min_variant_size)
        
        else:
            raise ValueError(f"Unsupported type kind: {type_def['type']['kind']}")
    
    def get_instruction_discriminators(self) -> Dict[str, bytes]:
        """Get a mapping of instruction names to their discriminators."""
        return {instr['name']: disc for disc, instr in self.instructions.items()}
    
    def get_instruction_names(self) -> list:
        """Get a list of all available instruction names."""
        return [instr['name'] for instr in self.instructions.values()]
    
    def validate_instruction_data_length(self, ix_data: bytes, discriminator: bytes) -> bool:
        """Validate that instruction data meets minimum length requirements."""
        if discriminator not in self.instruction_min_sizes:
            return True  # Allow if we don't know the expected size
        
        expected_min_size = self.instruction_min_sizes[discriminator]
        actual_size = len(ix_data)
        
        if actual_size < expected_min_size:
            instruction_name = self.instructions[discriminator]['name']
            if self.verbose:
                print(f"⚠️  Short {instruction_name} data ({actual_size}/{expected_min_size}B) - likely not token creation")
            return False
        
        return True
    
    def decode_instruction(self, ix_data: bytes, keys: list, accounts: list) -> Optional[Dict[str, Any]]:
        """Decode instruction data using IDL definitions."""
        if len(ix_data) < 8:
            return None
        
        discriminator = ix_data[:8]
        if discriminator not in self.instructions:
            return None
        
        # Validate instruction data length
        if not self.validate_instruction_data_length(ix_data, discriminator):
            return None
        
        instruction = self.instructions[discriminator]
        
        # Skip discriminator
        offset = 8
        data = ix_data[offset:]
        
        # Decode arguments
        args = {}
        decode_offset = 0
        
        for arg in instruction.get('args', []):
            try:
                value, decode_offset = self._decode_type(data, decode_offset, arg['type'])
                args[arg['name']] = value
            except Exception as e:
                if self.verbose:
                    print(f"❌ Decode error in {arg['name']}: {e}")
                return None
        
        # Extract account information
        def get_account_key(index):
            if index >= len(accounts):
                return "N/A"
            account_index = accounts[index]
            if account_index >= len(keys):
                return "N/A"
            return base58.b58encode(keys[account_index]).decode()
        
        # Build account info based on instruction definition
        account_info = {}
        instruction_accounts = instruction.get('accounts', [])
        for i, account_def in enumerate(instruction_accounts):
            account_info[account_def['name']] = get_account_key(i)
        
        return {
            'instruction_name': instruction['name'],
            'args': args,
            'accounts': account_info
        }
    
    def _decode_type(self, data: bytes, offset: int, type_def) -> tuple:
        """Decode a value based on its type definition."""
        if isinstance(type_def, str):
            return self._decode_primitive(data, offset, type_def)
        elif isinstance(type_def, dict):
            if 'defined' in type_def:
                # Handle both old and new IDL format
                if isinstance(type_def['defined'], dict):
                    return self._decode_defined_type(data, offset, type_def['defined']['name'])
                else:
                    return self._decode_defined_type(data, offset, type_def['defined'])
            else:
                raise ValueError(f"Unknown type definition: {type_def}")
        else:
            raise ValueError(f"Invalid type definition: {type_def}")
    
    def _decode_primitive(self, data: bytes, offset: int, type_name: str) -> tuple:
        """Decode primitive types."""
        if type_name == 'u8':
            return struct.unpack_from('<B', data, offset)[0], offset + 1
        elif type_name == 'u16':
            return struct.unpack_from('<H', data, offset)[0], offset + 2
        elif type_name == 'u32':
            return struct.unpack_from('<I', data, offset)[0], offset + 4
        elif type_name == 'u64':
            return struct.unpack_from('<Q', data, offset)[0], offset + 8
        elif type_name == 'string':
            length = struct.unpack_from('<I', data, offset)[0]
            offset += 4
            value = data[offset:offset + length].decode('utf-8')
            return value, offset + length
        elif type_name == 'pubkey':
            value = base58.b58encode(data[offset:offset + 32]).decode('utf-8')
            return value, offset + 32
        else:
            raise ValueError(f"Unknown primitive type: {type_name}")
    
    def _decode_defined_type(self, data: bytes, offset: int, type_name: str) -> tuple:
        """Decode user-defined types."""
        if type_name not in self.types:
            raise ValueError(f"Unknown type: {type_name}")
        
        type_def = self.types[type_name]
        
        if type_def['type']['kind'] == 'struct':
            struct_data = {}
            for field in type_def['type']['fields']:
                value, offset = self._decode_type(data, offset, field['type'])
                struct_data[field['name']] = value
            return struct_data, offset
        
        elif type_def['type']['kind'] == 'enum':
            # Read the discriminator (u8) to determine which variant
            if offset >= len(data):
                raise ValueError(f"Not enough data for enum discriminator at offset {offset}")
            
            variant_index = struct.unpack_from('<B', data, offset)[0]
            offset += 1
            
            variants = type_def['type']['variants']
            if variant_index >= len(variants):
                raise ValueError(f"Invalid enum variant index {variant_index} for type {type_name}")
            
            variant = variants[variant_index]
            variant_data = {"variant": variant['name']}
            
            # Decode variant fields if any
            for field in variant.get('fields', []):
                value, offset = self._decode_type(data, offset, field['type'])
                variant_data[field['name']] = value
            
            return variant_data, offset
        
        else:
            raise ValueError(f"Unsupported type kind: {type_def['type']['kind']}")


def load_idl_parser(idl_path: str, verbose: bool = False) -> IDLParser:
    """
    Convenience function to load an IDL parser.
    
    Args:
        idl_path: Path to the IDL JSON file
        verbose: Whether to print debug information
        
    Returns:
        Initialized IDLParser instance
    """
    return IDLParser(idl_path, verbose)
