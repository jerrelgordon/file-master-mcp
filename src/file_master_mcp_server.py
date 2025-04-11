"""
File Master MCP - Provides LLM access to files through MCP protocol.

Notes to self:
-------------
SSE Connection stuff:
- Using Starlette for SSE (it's lightweight and handles async well)
- Maybe add connection pooling?

Security:
- ALWAYS normalize paths - no exceptions (prevents path traversal)
- All security checks go through DirectoryValidator
- Watch out for Windows paths (backslashes are tricky)

Config:
- Everything security-related lives in config.json
- Default to safe mode (especially allow_delete=false)
- Double check paths against allowed_directories!

Random:
- FastMCP handles exposing tools to LLMs
- Keep responses consistent (Dict[str, Any])
- Needs Python 3.7+ (for asyncio)
- Key deps: uvicorn, starlette, fastmcp
- Two log files: mcp_server.log (ops) and security.log
- Security logger gets ALL path validations
"""
# Standard library imports
import os
import sys
import json
import logging
import asyncio
from datetime import datetime
from typing import Dict, List, Optional, Any
from urllib.parse import unquote
from collections import Counter

# Third-party imports
import uvicorn
from starlette.applications import Starlette
from starlette.responses import JSONResponse, StreamingResponse
from starlette.routing import Mount
from mcp.server.fastmcp import FastMCP
from mcp.types import Resource, Tool

# Configure logging
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(levelname)s - %(message)s',
    handlers=[
        logging.FileHandler('mcp_server.log'),
        logging.StreamHandler()
    ]
)
logger = logging.getLogger(__name__)

class ServerStartupTimeoutError(Exception):
    """Exception raised when server fails to start within configured timeout."""
    pass

class UvicornTimeoutServer(uvicorn.Server):
    """Custom Uvicorn server with timeout support."""
    
    def __init__(self, config: uvicorn.Config, timeout: int):
        super().__init__(config)
        self.timeout = timeout
        self._startup_complete = False
        
    async def startup(self, sockets: Optional[List] = None) -> None:
        """Override startup to track completion."""
        await super().startup(sockets)
        self._startup_complete = True
    
    async def run_with_timeout(self) -> None:
        """Run the server with a timeout for startup."""
        try:
            startup_task = asyncio.create_task(self.serve())
            
            # Wait for either startup completion or timeout
            try:
                await asyncio.wait_for(self._wait_for_startup(), timeout=self.timeout)
                logger.info(f"Server started successfully within {self.timeout} seconds")
                await startup_task  # Let the server continue running
            except asyncio.TimeoutError:
                logger.error(f"Server failed to start within {self.timeout} seconds")
                # Force shutdown
                await self.shutdown()
                raise ServerStartupTimeoutError(
                    f"Server failed to start within {self.timeout} seconds. "
                    "Check mcp_server.log and security.log for details."
                )
        except Exception as e:
            logger.error(f"Server startup error: {str(e)}")
            raise
    
    async def _wait_for_startup(self) -> None:
        """Wait for server startup to complete."""
        while not self._startup_complete:
            await asyncio.sleep(0.1)

class LogAccessManager:
    """Manages secure access to log files with strict security controls."""
    
    def __init__(self, config_path: str = "config.json"):
        """Initialize the log access manager with security controls."""
        self.config_path = config_path
        self.security_logger = self._setup_security_logger()
        self.config = self._load_config()
        self._validate_config()
        self._log_security_event("INFO", "LogAccessManager initialized with security controls")
    
    def _load_config(self) -> Dict[str, Any]:
        """Load configuration from config.json with security checks."""
        try:
            if not os.path.exists(self.config_path):
                logger.info(f"Config file not found: {self.config_path}")
                return {
                    "allowed_directories": [],
                    "max_file_size_mb": 10,
                    "supported_extensions": [".log", ".txt"],
                    "server_startup_timeout_seconds": 30,
                    "server_host": "127.0.0.1",
                    "server_port": 6466,
                    "allow_delete": False  # Default to false for safety
                }
            
            with open(self.config_path, 'r') as f:
                config = json.load(f)
            
            # Ensure allow_delete is present
            if "allow_delete" not in config:
                config["allow_delete"] = False  # Default to false if not specified
            
            logger.info(f"Configuration loaded from {self.config_path}")
            return config
            
        except Exception as e:
            logger.error(f"Failed to load configuration: {str(e)}")
            raise
    
    def _setup_security_logger(self) -> logging.Logger:
        """Set up a dedicated security logger with strict formatting."""
        logger = logging.getLogger("security")
        logger.setLevel(logging.INFO)
        
        # Create security log directory if it doesn't exist
        log_dir = os.path.dirname(os.path.abspath(__file__))
        security_log_path = os.path.join(log_dir, "..", "security.log")
        
        handler = logging.FileHandler(security_log_path)
        formatter = logging.Formatter(
            '%(asctime)s - %(levelname)s - %(message)s',
            datefmt='%Y-%m-%d %H:%M:%S'
        )
        handler.setFormatter(formatter)
        logger.addHandler(handler)
        
        return logger
    
    def _validate_config(self) -> None:
        """Validate configuration with security checks."""
        try:
            if not isinstance(self.config, dict):
                raise ValueError("Invalid config format")
            
            if "allowed_directories" not in self.config:
                raise ValueError("Missing allowed_directories in config")
            
            if not isinstance(self.config["allowed_directories"], list):
                raise ValueError("allowed_directories must be a list")
            
            # Validate each directory
            for directory in self.config["allowed_directories"]:
                if not isinstance(directory, str):
                    raise ValueError(f"Invalid directory path: {directory}")
                if not os.path.isabs(directory):
                    raise ValueError(f"Directory must be absolute path: {directory}")
                if not os.path.exists(directory):
                    raise ValueError(f"Directory does not exist: {directory}")
                if not os.access(directory, os.R_OK):
                    raise ValueError(f"Directory not readable: {directory}")
            
            # Validate file size limit
            if "max_file_size_mb" not in self.config:
                raise ValueError("Missing max_file_size_mb in config")
            if not isinstance(self.config["max_file_size_mb"], (int, float)):
                raise ValueError("max_file_size_mb must be a number")
            if self.config["max_file_size_mb"] <= 0:
                raise ValueError("max_file_size_mb must be positive")
            
            # Validate supported extensions
            if "supported_extensions" not in self.config:
                raise ValueError("Missing supported_extensions in config")
            if not isinstance(self.config["supported_extensions"], list):
                raise ValueError("supported_extensions must be a list")
            for ext in self.config["supported_extensions"]:
                if not isinstance(ext, str) or not ext.startswith('.'):
                    raise ValueError(f"Invalid file extension: {ext}")
            
            # Validate server host
            if "server_host" not in self.config:
                raise ValueError("Missing server_host in config")
            host = self.config["server_host"]
            if not isinstance(host, str):
                raise ValueError("server_host must be a string")
            
            # Validate server port
            if "server_port" not in self.config:
                raise ValueError("Missing server_port in config")
            port = self.config["server_port"]
            if not isinstance(port, int):
                raise ValueError("server_port must be an integer")
            if port < 1 or port > 65535:
                raise ValueError("server_port must be between 1 and 65535")
            
            # Validate allow_delete
            if "allow_delete" not in self.config:
                raise ValueError("Missing allow_delete in config")
            if not isinstance(self.config["allow_delete"], bool):
                raise ValueError("allow_delete must be a boolean")
            
            self._log_security_event("INFO", "Configuration validated successfully")
            
        except Exception as e:
            self._log_security_event("ERROR", f"Configuration validation failed: {str(e)}")
            raise
    
    def _normalize_path(self, path: str) -> str:
        """Normalize and sanitize file path with security checks."""
        try:
            # Remove URL encoding
            path = unquote(path)
            
            # Remove null bytes and control characters
            path = ''.join(char for char in path if ord(char) >= 32)
            
            # Normalize path
            path = os.path.normpath(path)
            
            # Ensure path is absolute
            if not os.path.isabs(path):
                path = os.path.abspath(path)
            
            return path
            
        except Exception as e:
            self._log_security_event("ERROR", f"Path normalization failed: {str(e)}")
            raise ValueError("Invalid path")
    
    def _is_path_allowed(self, path: str, is_directory: bool = False) -> bool:
        """Check if path is allowed with strict security rules."""
        try:
            normalized_path = self._normalize_path(path)
            
            # Silently ignore hidden files and directories
            if os.path.basename(normalized_path).startswith('.'):
                return False
            
            # Check if path is within allowed directories
            for allowed_dir in self.config["allowed_directories"]:
                if normalized_path.startswith(allowed_dir):
                    # Additional security checks
                    if not os.path.exists(normalized_path):
                        self._log_security_event("WARNING", f"Path does not exist: {normalized_path}")
                        return False
                    
                    if not os.access(normalized_path, os.R_OK):
                        self._log_security_event("WARNING", f"Path not readable: {normalized_path}")
                        return False
                    
                    # For files, check extension
                    if not is_directory and os.path.isfile(normalized_path):
                        _, ext = os.path.splitext(normalized_path)
                        if ext not in self.config["supported_extensions"]:
                            self._log_security_event("WARNING", f"Unsupported file extension: {ext}")
                            return False
                    
                    return True
            
            self._log_security_event("WARNING", f"Path not within allowed directories: {normalized_path}")
            return False
            
        except Exception as e:
            self._log_security_event("ERROR", f"Path validation failed: {str(e)}")
            return False
    
    def _log_security_event(self, level: str, message: str) -> None:
        """Log security events with strict formatting."""
        try:
            if level.upper() == "INFO":
                self.security_logger.info(message)
            elif level.upper() == "WARNING":
                self.security_logger.warning(message)
            elif level.upper() == "ERROR":
                self.security_logger.error(message)
            else:
                self.security_logger.info(f"Unknown level: {message}")
        except Exception as e:
            logger.error(f"Failed to log security event: {str(e)}")
    
    def read_file(self, path: str) -> Optional[str]:
        """Read file contents with security checks."""
        try:
            if not self._is_path_allowed(path):
                self._log_security_event("WARNING", f"Access denied to file: {path}")
                return None
            
            with open(path, 'r', encoding='utf-8') as f:
                content = f.read()
            
            self._log_security_event("INFO", f"Successfully read file: {path}")
            return content
            
        except Exception as e:
            self._log_security_event("ERROR", f"Failed to read file {path}: {str(e)}")
            return None
    
    def list_files(self, directory: str) -> List[Dict[str, Any]]:
        """List all files in a directory and its subdirectories with security checks."""
        try:
            normalized_dir = self._normalize_path(directory)
            if not self._is_path_allowed(normalized_dir, is_directory=True):
                return []
            
            result = []
            for root, dirs, files in os.walk(normalized_dir):
                # Skip hidden directories
                dirs[:] = [d for d in dirs if not d.startswith('.')]
                
                # Process files in current directory
                for file in files:
                    if file.startswith('.'):  # Skip hidden files
                        continue
                    
                    file_path = os.path.join(root, file)
                    if self._is_path_allowed(file_path, is_directory=False):
                        try:
                            stat = os.stat(file_path)
                            result.append({
                                "name": file,
                                "path": file_path,
                                "size": stat.st_size,
                                "modified": datetime.fromtimestamp(stat.st_mtime).isoformat(),
                                "type": os.path.splitext(file)[1]
                            })
                        except Exception as e:
                            self._log_security_event("WARNING", f"Failed to get file info: {str(e)}")
            
            if result:
                self._log_security_event("INFO", f"Successfully listed {len(result)} files in directory: {normalized_dir}")
            return result
            
        except Exception as e:
            self._log_security_event("ERROR", f"Failed to list files: {str(e)}")
            return []
    
    def search_files(self, directory: str, pattern: str) -> List[Dict[str, Any]]:
        """Search files for pattern with security checks."""
        try:
            normalized_dir = self._normalize_path(directory)
            if not self._is_path_allowed(normalized_dir, is_directory=True):
                self._log_security_event("WARNING", f"Access denied to directory: {directory}")
                return []
            
            results = []
            for root, dirs, files in os.walk(normalized_dir):
                # Skip hidden directories
                dirs[:] = [d for d in dirs if not d.startswith('.')]
                
                # Process files in current directory
                for file in files:
                    if file.startswith('.'):  # Skip hidden files
                        continue
                    
                    file_path = os.path.join(root, file)
                    if self._is_path_allowed(file_path, is_directory=False):
                        try:
                            with open(file_path, 'r', encoding='utf-8') as f:
                                content = f.read()
                                if pattern in content:
                                    results.append({
                                        "file": file,
                                        "path": file_path,
                                        "matches": len(content.split(pattern)) - 1
                                    })
                        except Exception as e:
                            self._log_security_event("ERROR", f"Failed to search file {file_path}: {str(e)}")
                            continue
            
            if results:
                self._log_security_event("INFO", f"Successfully found {len(results)} files containing pattern '{pattern}' in directory: {normalized_dir}")
            return results
            
        except Exception as e:
            self._log_security_event("ERROR", f"Failed to search files in {directory}: {str(e)}")
            return []
    
    def analyze_directory(self, directory: str) -> Dict[str, Any]:
        """Analyze directory contents with security checks."""
        try:
            normalized_dir = self._normalize_path(directory)
            if not self._is_path_allowed(normalized_dir, is_directory=True):
                self._log_security_event("WARNING", f"Access denied to directory: {directory}")
                return {}
            
            analysis = {
                "total_files": 0,
                "total_size": 0,
                "file_types": Counter(),
                "log_levels": Counter(),
                "recent_errors": [],
                "files": []
            }
            
            # Walk through directory tree
            for root, dirs, files in os.walk(normalized_dir):
                # Skip hidden directories
                dirs[:] = [d for d in dirs if not d.startswith('.')]
                
                # Process files in current directory
                for file in files:
                    if file.startswith('.'):  # Skip hidden files
                        continue
                    
                    file_path = os.path.join(root, file)
                    if self._is_path_allowed(file_path, is_directory=False):
                        try:
                            stat = os.stat(file_path)
                            file_size = stat.st_size
                            
                            analysis["total_files"] += 1
                            analysis["total_size"] += file_size
                            analysis["file_types"][os.path.splitext(file)[1]] += 1
                            
                            with open(file_path, 'r', encoding='utf-8') as f:
                                content = f.readlines()
                                
                                # Analyze log levels
                                for line in content:
                                    if "ERROR" in line:
                                        analysis["log_levels"]["ERROR"] += 1
                                        analysis["recent_errors"].append({
                                            "file": file,
                                            "line": line.strip(),
                                            "time": datetime.fromtimestamp(stat.st_mtime).isoformat()
                                        })
                                    elif "WARNING" in line:
                                        analysis["log_levels"]["WARNING"] += 1
                                    elif "INFO" in line:
                                        analysis["log_levels"]["INFO"] += 1
                                
                                # Add file details
                                analysis["files"].append({
                                    "name": file,
                                    "path": file_path,
                                    "size": file_size,
                                    "lines": len(content),
                                    "modified": datetime.fromtimestamp(stat.st_mtime).isoformat()
                                })
                                
                        except Exception as e:
                            self._log_security_event("ERROR", f"Failed to analyze file {file_path}: {str(e)}")
                            continue
            
            # Keep only the 10 most recent errors
            analysis["recent_errors"] = sorted(
                analysis["recent_errors"],
                key=lambda x: x["time"],
                reverse=True
            )[:10]
            
            self._log_security_event("INFO", f"Successfully analyzed directory: {directory}")
            return analysis
            
        except Exception as e:
            self._log_security_event("ERROR", f"Failed to analyze directory {directory}: {str(e)}")
            return {}
    
    def get_allowed_directories(self) -> List[str]:
        """Get all allowed log directories."""
        return self.config["allowed_directories"]

    def create_directory(self, directory: str) -> bool:
        """Create a new directory with security checks."""
        try:
            normalized_path = self._normalize_path(directory)
            
            # Check if path is within allowed directories
            allowed = False
            for allowed_dir in self.config["allowed_directories"]:
                if normalized_path.startswith(allowed_dir):
                    allowed = True
                    break
            
            if not allowed:
                self._log_security_event("WARNING", f"Attempted to create directory outside allowed paths: {normalized_path}")
                return False
            
            # Check if directory already exists
            if os.path.exists(normalized_path):
                self._log_security_event("WARNING", f"Directory already exists: {normalized_path}")
                return False
            
            # Create the directory
            os.makedirs(normalized_path, exist_ok=True)
            self._log_security_event("INFO", f"Created directory: {normalized_path}")
            return True
            
        except Exception as e:
            self._log_security_event("ERROR", f"Failed to create directory: {str(e)}")
            return False

    def create_file(self, file_path: str, content: str = "") -> bool:
        """Create a new file with security checks."""
        try:
            normalized_path = self._normalize_path(file_path)
            
            # Check if path is within allowed directories
            allowed = False
            for allowed_dir in self.config["allowed_directories"]:
                if normalized_path.startswith(allowed_dir):
                    allowed = True
                    break
            
            if not allowed:
                self._log_security_event("WARNING", f"Attempted to create file outside allowed paths: {normalized_path}")
                return False
            
            # Check file extension
            _, ext = os.path.splitext(normalized_path)
            if ext not in self.config["supported_extensions"]:
                self._log_security_event("WARNING", f"Attempted to create file with unsupported extension: {ext}")
                return False
            
            # Check if file already exists
            if os.path.exists(normalized_path):
                self._log_security_event("WARNING", f"File already exists: {normalized_path}")
                return False
            
            # Create parent directories if they don't exist
            os.makedirs(os.path.dirname(normalized_path), exist_ok=True)
            
            # Create the file with content
            with open(normalized_path, 'w') as f:
                f.write(content)
            
            self._log_security_event("INFO", f"Created file: {normalized_path}")
            return True
            
        except Exception as e:
            self._log_security_event("ERROR", f"Failed to create file: {str(e)}")
            return False

    def move_file(self, source_path: str, target_path: str) -> bool:
        """Move a file with security checks."""
        try:
            # Normalize paths
            source_normalized = self._normalize_path(source_path)
            target_normalized = self._normalize_path(target_path)
            
            # Check if source exists and is a file
            if not os.path.exists(source_normalized):
                self._log_security_event("WARNING", f"Source file does not exist: {source_normalized}")
                return False
            if not os.path.isfile(source_normalized):
                self._log_security_event("WARNING", f"Source path is not a file: {source_normalized}")
                return False
            
            # Check if source and target are in allowed directories
            source_allowed = False
            target_allowed = False
            for allowed_dir in self.config["allowed_directories"]:
                if source_normalized.startswith(allowed_dir):
                    source_allowed = True
                if target_normalized.startswith(allowed_dir):
                    target_allowed = True
            
            if not source_allowed:
                self._log_security_event("WARNING", f"Source file outside allowed paths: {source_normalized}")
                return False
            if not target_allowed:
                self._log_security_event("WARNING", f"Target path outside allowed paths: {target_normalized}")
                return False
            
            # Check file extension
            _, ext = os.path.splitext(target_normalized)
            if ext not in self.config["supported_extensions"]:
                self._log_security_event("WARNING", f"Target file has unsupported extension: {ext}")
                return False
            
            # Check if target already exists
            if os.path.exists(target_normalized):
                self._log_security_event("WARNING", f"Target file already exists: {target_normalized}")
                return False
            
            # Create target directory if it doesn't exist
            target_dir = os.path.dirname(target_normalized)
            os.makedirs(target_dir, exist_ok=True)
            
            # Move the file
            os.rename(source_normalized, target_normalized)
            self._log_security_event("INFO", f"Moved file from {source_normalized} to {target_normalized}")
            return True
            
        except Exception as e:
            self._log_security_event("ERROR", f"Failed to move file: {str(e)}")
            return False

    def move_directory(self, source_path: str, target_path: str) -> bool:
        """Move a directory with security checks."""
        try:
            # Normalize paths
            source_normalized = self._normalize_path(source_path)
            target_normalized = self._normalize_path(target_path)
            
            # Check if source exists and is a directory
            if not os.path.exists(source_normalized):
                self._log_security_event("WARNING", f"Source directory does not exist: {source_normalized}")
                return False
            if not os.path.isdir(source_normalized):
                self._log_security_event("WARNING", f"Source path is not a directory: {source_normalized}")
                return False
            
            # Check if source and target are in allowed directories
            source_allowed = False
            target_allowed = False
            for allowed_dir in self.config["allowed_directories"]:
                if source_normalized.startswith(allowed_dir):
                    source_allowed = True
                if target_normalized.startswith(allowed_dir):
                    target_allowed = True
            
            if not source_allowed:
                self._log_security_event("WARNING", f"Source directory outside allowed paths: {source_normalized}")
                return False
            if not target_allowed:
                self._log_security_event("WARNING", f"Target path outside allowed paths: {target_normalized}")
                return False
            
            # Check if target already exists
            if os.path.exists(target_normalized):
                self._log_security_event("WARNING", f"Target directory already exists: {target_normalized}")
                return False
            
            # Move the directory
            os.rename(source_normalized, target_normalized)
            self._log_security_event("INFO", f"Moved directory from {source_normalized} to {target_normalized}")
            return True
            
        except Exception as e:
            self._log_security_event("ERROR", f"Failed to move directory: {str(e)}")
            return False

    def delete_file(self, file_path: str) -> bool:
        """Delete a file with security checks."""
        try:
            # Check if deletion is allowed
            if not self.config["allow_delete"]:
                self._log_security_event("WARNING", "File deletion is disabled in configuration")
                return False

            normalized_path = self._normalize_path(file_path)
            
            # Check if path is within allowed directories
            if not self._is_path_allowed(normalized_path):
                self._log_security_event("WARNING", f"Attempted to delete file outside allowed paths: {normalized_path}")
                return False
            
            # Check if file exists and is a file
            if not os.path.exists(normalized_path):
                self._log_security_event("WARNING", f"File does not exist: {normalized_path}")
                return False
            if not os.path.isfile(normalized_path):
                self._log_security_event("WARNING", f"Path is not a file: {normalized_path}")
                return False
            
            # Delete the file
            os.remove(normalized_path)
            self._log_security_event("INFO", f"Deleted file: {normalized_path}")
            return True
            
        except Exception as e:
            self._log_security_event("ERROR", f"Failed to delete file: {str(e)}")
            return False

    def delete_directory(self, directory: str) -> bool:
        """Delete a directory with security checks."""
        try:
            # Check if deletion is allowed
            if not self.config["allow_delete"]:
                self._log_security_event("WARNING", "Directory deletion is disabled in configuration")
                return False

            normalized_path = self._normalize_path(directory)
            
            # Check if path is within allowed directories
            if not self._is_path_allowed(normalized_path, is_directory=True):
                self._log_security_event("WARNING", f"Attempted to delete directory outside allowed paths: {normalized_path}")
                return False
            
            # Check if directory exists and is a directory
            if not os.path.exists(normalized_path):
                self._log_security_event("WARNING", f"Directory does not exist: {normalized_path}")
                return False
            if not os.path.isdir(normalized_path):
                self._log_security_event("WARNING", f"Path is not a directory: {normalized_path}")
                return False
            
            # Delete the directory and its contents
            import shutil
            shutil.rmtree(normalized_path)
            self._log_security_event("INFO", f"Deleted directory: {normalized_path}")
            return True
            
        except Exception as e:
            self._log_security_event("ERROR", f"Failed to delete directory: {str(e)}")
            return False

    def list_directories(self, directory: str) -> List[Dict[str, Any]]:
        """List all directories in the given path."""
        try:
            normalized_path = os.path.normpath(directory)
            if not self._is_path_allowed(normalized_path, is_directory=True):
                self._log_security_event("WARNING", f"Access denied to directory: {directory}")
                return []

            directories = []
            for root, dirs, _ in os.walk(normalized_path):
                for dir_name in dirs:
                    dir_path = os.path.join(root, dir_name)
                    # Skip hidden directories
                    if dir_name.startswith('.'):
                        continue
                    try:
                        stat = os.stat(dir_path)
                        directories.append({
                            "name": dir_name,
                            "path": dir_path,
                            "modified": datetime.fromtimestamp(stat.st_mtime).isoformat(),
                            "is_empty": len(os.listdir(dir_path)) == 0
                        })
                    except Exception as e:
                        self._log_security_event("ERROR", f"Failed to get directory info: {str(e)}")
            return directories
        except Exception as e:
            self._log_security_event("ERROR", f"Failed to list directories: {str(e)}")
            return []

class DirectoryValidator:
    """Base class for directory validation logic.
    
    Note to self: This is the security gatekeeper - EVERYTHING goes through here.
    If you're thinking about bypassing this, you're probably doing something wrong.
    
    Ideas for later:
    - Cache frequently checked paths?
    - Add regex patterns for paths?
    - Maybe add different permission levels per directory????
    """
    
    def __init__(self, access_manager):
        self.access_manager = access_manager
    
    def validate_path(self, path: str, is_directory: bool = False) -> bool:
        """Validate a path with security checks."""
        try:
            normalized_path = os.path.normpath(path)
            # Check if path is under any allowed directory
            for allowed_dir in self.access_manager.config["allowed_directories"]:
                allowed_dir = os.path.normpath(allowed_dir)
                if normalized_path.startswith(allowed_dir):
                    return True
            self.access_manager._log_security_event("WARNING", f"Path not in allowed directories: {path}")
            return False
        except Exception as e:
            self.access_manager._log_security_event("ERROR", f"Path validation failed: {str(e)}")
            return False
    
    def validate_paths(self, *paths: str, is_directory: bool = False) -> bool:
        """Validate multiple paths."""
        return all(self.validate_path(path, is_directory) for path in paths)

class SecureFileOperation(DirectoryValidator):
    """Base class for secure file operations."""
    
    def __init__(self, access_manager):
        super().__init__(access_manager)
    
    def check_delete_permission(self) -> bool:
        """Check if deletion operations are allowed."""
        if not self.access_manager.config["allow_delete"]:
            self.access_manager._log_security_event("WARNING", "Deletion operations are disabled")
            return False
        return True

class LogMCPServer:
    """Main server class that coordinates MCP, logging, and security.
    
    Note to self: This is where everything connects:
    - FastMCP does protocol stuff
    - DirectoryValidator handles security
    - LogAccessManager does file operations
    - Order matters when registering tools
    - SSE connections can timeout
    - Make sure security logger starts first
    """
    
    def __init__(self, config_path: str = "config.json"):
        """Initialize the server components."""
        # Set up logging
        self._setup_logging()
        
        # Initialize components
        self.access_manager = LogAccessManager(config_path)
        self.mcp = FastMCP("File Master MCP")
        self.server_instance = None
        
        # Initialize secure operations
        self.secure_ops = SecureFileOperation(self.access_manager)
        
        # Register MCP endpoints
        self._register_mcp_endpoints()
    
    def _setup_logging(self):
        """Configure logging for the server."""
        self.logger = logging.getLogger(__name__)
    
    def _register_mcp_endpoints(self):
        """Register all MCP resources and tools.
        
        Notes for later:
        - Each new tool needs security boundaries
        - Keep error responses consistent
        - Don't forget to log everything
        - Use large datatsets to testperformance
        
        Remember: LLMs discover tools dynamically,
        so good docs are super important!
        """
        # Resources
        @self.mcp.resource("logs://directories")
        def list_log_directories() -> Resource:
            """List all configured log directories"""
            directories = self.access_manager.get_allowed_directories()
            return Resource(
                content=json.dumps(directories, indent=2),
                content_type="application/json",
                description="List of configured log directories"
            )
        
        @self.mcp.resource("logs://{path}")
        def read_log_file(path: str) -> Resource:
            """Read a log file with security checks."""
            try:
                content = self.access_manager.read_file(path)
                if content is None:
                    return Resource(
                        content="Access denied or file not found",
                        content_type="text/plain",
                        description="Log file access result"
                    )
                
                return Resource(
                    content=content,
                    content_type="text/plain",
                    description=f"Contents of log file: {path}"
                )
            except Exception as e:
                self.logger.error(f"Error reading log file: {str(e)}")
                return Resource(
                    content=f"Error reading file: {str(e)}",
                    content_type="text/plain",
                    description="Error reading log file"
                )
        
        # Tools
        @self.mcp.tool()
        def get_files(directory: str) -> List[Dict[str, Any]]:
            """List all log files in a directory with security checks."""
            try:
                if not self.secure_ops.validate_path(directory, is_directory=True):
                    self.logger.error(f"Access denied to directory: {directory}")
                    return []
                return self.access_manager.list_files(directory)
            except Exception as e:
                self.logger.error(f"Error listing files: {str(e)}")
                return []
        
        @self.mcp.tool()
        def search_files(directory: str, pattern: str) -> List[Dict[str, Any]]:
            """Search for a pattern in log files with security checks."""
            try:
                if not self.secure_ops.validate_path(directory, is_directory=True):
                    self.logger.error(f"Access denied to directory: {directory}")
                    return []
                return self.access_manager.search_files(directory, pattern)
            except Exception as e:
                self.logger.error(f"Error searching files: {str(e)}")
                return []
        
        @self.mcp.tool()
        def get_files_content(directory: str) -> Dict[str, Any]:
            """Get detailed content and analysis of all log files in a directory."""
            try:
                if not self.secure_ops.validate_path(directory, is_directory=True):
                    self.logger.error(f"Access denied to directory: {directory}")
                    return {}
                return self.access_manager.analyze_directory(directory)
            except Exception as e:
                self.logger.error(f"Error analyzing directory: {str(e)}")
                return {}
        
        @self.mcp.tool()
        def create_directory(directory: str) -> Dict[str, Any]:
            """Create a new directory in an allowed location."""
            try:
                if not self.secure_ops.validate_path(directory, is_directory=True):
                    return {"success": False, "message": "Access denied or invalid path"}
                success = self.access_manager.create_directory(directory)
                return {
                    "success": success,
                    "message": "Directory created successfully" if success else "Failed to create directory"
                }
            except Exception as e:
                self.logger.error(f"Error creating directory: {str(e)}")
                return {"success": False, "message": f"Error: {str(e)}"}
        
        @self.mcp.tool()
        def create_file(file_path: str, content: str = "") -> Dict[str, Any]:
            """Create a new file in an allowed location with optional content."""
            try:
                if not self.secure_ops.validate_path(file_path):
                    return {"success": False, "message": "Access denied or invalid path"}
                success = self.access_manager.create_file(file_path, content)
                return {
                    "success": success,
                    "message": "File created successfully" if success else "Failed to create file"
                }
            except Exception as e:
                self.logger.error(f"Error creating file: {str(e)}")
                return {"success": False, "message": f"Error: {str(e)}"}
        
        @self.mcp.tool()
        def move_file(source_path: str, target_path: str) -> Dict[str, Any]:
            """Move a file from source path to target path with security checks."""
            try:
                if not self.secure_ops.validate_paths(source_path, target_path):
                    return {"success": False, "message": "Access denied or invalid paths"}
                success = self.access_manager.move_file(source_path, target_path)
                return {
                    "success": success,
                    "message": "File moved successfully" if success else "Failed to move file",
                    "source": source_path,
                    "target": target_path
                }
            except Exception as e:
                self.logger.error(f"Error moving file: {str(e)}")
                return {"success": False, "message": f"Error: {str(e)}"}
        
        @self.mcp.tool()
        def move_directory(source_path: str, target_path: str) -> Dict[str, Any]:
            """Move a directory from source path to target path with security checks."""
            try:
                if not self.secure_ops.validate_paths(source_path, target_path, is_directory=True):
                    return {"success": False, "message": "Access denied or invalid paths"}
                success = self.access_manager.move_directory(source_path, target_path)
                return {
                    "success": success,
                    "message": "Directory moved successfully" if success else "Failed to move directory",
                    "source": source_path,
                    "target": target_path
                }
            except Exception as e:
                self.logger.error(f"Error moving directory: {str(e)}")
                return {"success": False, "message": f"Error: {str(e)}"}
        
        @self.mcp.tool()
        def delete_file(file_path: str) -> Dict[str, Any]:
            """Delete a file with security checks."""
            try:
                if not self.secure_ops.check_delete_permission():
                    return {"success": False, "message": "Delete operations are disabled"}
                if not self.secure_ops.validate_path(file_path):
                    return {"success": False, "message": "Access denied or invalid path"}
                success = self.access_manager.delete_file(file_path)
                return {
                    "success": success,
                    "message": "File deleted successfully" if success else "Failed to delete file",
                    "path": file_path
                }
            except Exception as e:
                self.logger.error(f"Error deleting file: {str(e)}")
                return {"success": False, "message": f"Error: {str(e)}"}
        
        @self.mcp.tool()
        def delete_directory(directory: str) -> Dict[str, Any]:
            """Delete a directory with security checks."""
            try:
                if not self.secure_ops.check_delete_permission():
                    return {"success": False, "message": "Delete operations are disabled"}
                if not self.secure_ops.validate_path(directory, is_directory=True):
                    return {"success": False, "message": "Access denied or invalid path"}
                success = self.access_manager.delete_directory(directory)
                return {
                    "success": success,
                    "message": "Directory deleted successfully" if success else "Failed to delete directory",
                    "path": directory
                }
            except Exception as e:
                self.logger.error(f"Error deleting directory: {str(e)}")
                return {"success": False, "message": f"Error: {str(e)}"}
        
        @self.mcp.tool()
        def get_directories(directory: str) -> List[Dict[str, Any]]:
            """List all directories (including empty ones) with security checks."""
            try:
                if not self.secure_ops.validate_path(directory, is_directory=True):
                    self.logger.error(f"Access denied to directory: {directory}")
                    return []
                return self.access_manager.list_directories(directory)
            except Exception as e:
                self.logger.error(f"Error listing directories: {str(e)}")
                return []
    
    def start(self):
        """Start the MCP server."""
        try:
            self.logger.info("Starting MCP server...")
            config = self.access_manager.config
            timeout = config.get("server_startup_timeout_seconds", 30)
            host = config["server_host"]
            port = config["server_port"]
            
            self.logger.info(f"Configuring server with host={host}, port={port}")
            
            # Configure Uvicorn
            uvicorn_config = uvicorn.Config(
                self.mcp.sse_app(),
                host=host,
                port=port,
                log_level="info",
                log_config=None
            )
            
            # Create and run server with timeout
            self.server_instance = UvicornTimeoutServer(uvicorn_config, timeout)
            asyncio.run(self.server_instance.run_with_timeout())
            
        except ServerStartupTimeoutError as e:
            self.logger.error(str(e))
            sys.exit(1)
        except Exception as e:
            self.logger.error(f"Server error: {str(e)}")
            sys.exit(1)
    
    def stop(self):
        """Stop the MCP server."""
        if self.server_instance:
            asyncio.run(self.server_instance.shutdown())
            self.server_instance = None
            self.logger.info("Server stopped.")

def main():
    """Main entry point for the server."""
    server = LogMCPServer()
    server.start()

if __name__ == "__main__":
    main() 