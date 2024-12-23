import os
import datetime
import logging
import re
import tempfile
import zipfile
from concurrent.futures import ThreadPoolExecutor
from typing import List, Callable, Optional
import threading

# Configure logging
logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(levelname)s - %(message)s')

class FileCombiner:
    """Combines multiple files into a single output file, with various options."""

    def __init__(self):
        self.source_dir = "."
        self.output_file = "combined_files.txt"
        self.extensions: List[str] = []  # List of file extensions to include
        self.exclude_folders: List[str] = ['.git']  # List of folders to exclude
        self.exclude_patterns: List[str] = []  # List of regex patterns to exclude from file paths
        self.include_line_numbers: bool = False  # Whether to include line numbers in the output
        self.include_timestamp: bool = False  # Whether to include file modification timestamp
        self.include_file_size: bool = False  # Whether to include file size
        self.add_syntax_highlight: bool = False  # Whether to add basic syntax highlighting (requires manual language spec)
        self.max_file_size_mb: Optional[float] = None  # Maximum file size (MB) to include
        self.create_zip_archive: bool = False  # Whether to create a zip archive of the output
        self.exclude_images: bool = False  # Whether to exclude common image files
        self.exclude_executable: bool = False  # Whether to exclude executable files
        self.exclude_temp_and_backup_files: bool = False  # Whether to exclude temp and backup files
        self.exclude_hidden_files: bool = False  # Whether to exclude hidden files
        self.num_worker_threads: int = 4  # Number of threads to use for processing
        self.lock = threading.Lock()  # Lock for thread-safe file writing

        self._image_extensions = ['.jpg', '.jpeg', '.png', '.gif', '.bmp', '.tiff']
        self._temp_backup_extensions = ['.tmp', '.temp', '.bak', '~']

    def get_user_preferences(self):
        """Interactively gets user preferences for file combination."""
        print("\n=== File Combiner Configuration ===")

        self.source_dir = self._get_input("Source directory (default: .): ", self.source_dir, os.path.isdir)
        self.output_file = self._get_input("Output file name (default: combined_files.txt): ", self.output_file)
        self.extensions = self._get_list_input("File extensions to include (comma-separated, or Enter for all): ")
        self.exclude_folders = self._get_list_input("Folders to exclude (comma-separated, default: .git): ", self.exclude_folders)
        self.exclude_patterns = self._get_list_input("Regex patterns to exclude (comma-separated): ")

        self.include_line_numbers = self._get_boolean_input("Include line numbers? (y/n): ")
        self.include_timestamp = self._get_boolean_input("Include timestamps? (y/n): ")
        self.include_file_size = self._get_boolean_input("Include file sizes? (y/n): ")
        self.add_syntax_highlight = self._get_boolean_input("Add syntax highlighting (requires manual language spec)? (y/n): ")
        self.max_file_size_mb = self._get_float_input("Max file size to include (MB, or Enter for no limit): ")
        self.create_zip_archive = self._get_boolean_input("Create zip archive of output? (y/n): ")
        self.exclude_images = self._get_boolean_input("Exclude common image files (basic check, not fully reliable)? (y/n): ")
        self.exclude_executable = self._get_boolean_input("Exclude executable files? (y/n): ")
        self.exclude_temp_and_backup_files = self._get_boolean_input("Exclude temp/backup files? (y/n): ")
        self.exclude_hidden_files = self._get_boolean_input("Exclude hidden files? (y/n): ")
        self.num_worker_threads = self._get_int_input("Number of worker threads (default: 4): ", 4, lambda x: x > 0)

    def _get_input(self, prompt: str, default: Optional[str] = None, validator: Callable[[str], bool] = lambda x: True) -> str:
        """Gets validated string input from the user."""
        while True:
            value = input(prompt).strip()
            if not value:
                return default if default is not None else "" # Handle case where default is explicitly None
            if validator(value):
                return value
            print("Invalid input.")

    def _get_list_input(self, prompt: str, default: Optional[List[str]] = None) -> List[str]:
        """Gets a comma-separated list from the user."""
        value = input(prompt).strip()
        if not value:
            return default or []
        return [x.strip() for x in value.split(',') if x.strip()]

    def _get_boolean_input(self, prompt: str) -> bool:
        """Gets a boolean response from the user."""
        return self._get_input(prompt, "n", lambda x: x.lower() in ('y', 'n')) == 'y'

    def _get_float_input(self, prompt: str) -> Optional[float]:
        """Gets a float response from the user or None for no input"""
        while True:
            value = input(prompt).strip()
            if not value:
                return None
            try:
                return float(value)
            except ValueError:
                print("Invalid input. Please enter a number.")

    def _get_int_input(self, prompt: str, default: int, validator: Callable[[int], bool] = lambda x: True) -> int:
       """Gets a validated integer input from the user."""
       while True:
           value = input(prompt).strip()
           if not value:
               return default
           try:
               int_value = int(value)
               if validator(int_value):
                   return int_value
               else:
                   print("Invalid input. Value does not meet criteria.")
           except ValueError:
               print("Invalid input. Please enter an integer.")

    def should_process_file(self, filepath: str) -> bool:
        """Determines if a file should be processed based on user settings."""
        try:
            if self.extensions and not any(filepath.endswith(ext) for ext in self.extensions):
                return False
            if any(folder in filepath for folder in self.exclude_folders):
                return False
            if self.exclude_patterns and any(re.search(pattern, filepath) for pattern in self.exclude_patterns):
                return False
            file_size = os.path.getsize(filepath)
            if self.max_file_size_mb is not None and file_size > self.max_file_size_mb * 1024 * 1024:
                return False
            if self.exclude_images and any(filepath.lower().endswith(ext) for ext in self._image_extensions):
                return False
            if self.exclude_executable and os.access(filepath, os.X_OK):
                return False
            if self.exclude_temp_and_backup_files and (filepath.startswith(tempfile.gettempdir()) or any(filepath.endswith(ext) for ext in self._temp_backup_extensions)):
                return False
            if self.exclude_hidden_files and os.path.basename(filepath).startswith('.'):
                return False
            return True
        except OSError as e:
            logging.warning(f"Could not determine if file should be processed {filepath}: {e}")
            return False  # If any error occurs during checking, default to not processing

    def _process_file(self, filepath: str, outfile):
        """Processes and writes a single file to the output."""
        try:
            with open(filepath, 'r', encoding='utf-8', errors='ignore') as infile: # Handle potential encoding errors
                content = infile.read()
                with self.lock:  # Lock for multithreading
                    self._write_file_header(outfile, filepath)
                    if self.add_syntax_highlight:
                        ext = os.path.splitext(filepath)[1]
                        outfile.write(f"```{ext[1:] if ext else ''}\n")
                    if self.include_line_numbers:
                        for i, line in enumerate(content.splitlines(), 1):
                            outfile.write(f"{i:4d} | {line}\n")
                    else:
                        outfile.write(content)
                    if self.add_syntax_highlight:
                        outfile.write("```\n")
                    outfile.write("\n")
            return 1, os.path.getsize(filepath)
        except Exception as e:
            logging.error(f"Error reading {filepath}: {e}")
            with self.lock:
                outfile.write(f"Error reading {filepath}: {e}\n\n")
            return 0, 0

    def _write_summary(self, outfile):
        """Writes the initial summary information to the output file."""
        outfile.write("=== File Combination Summary ===\n")
        outfile.write(f"Generated on: {datetime.datetime.now()}\n")
        outfile.write(f"Source directory: {os.path.abspath(self.source_dir)}\n")
        outfile.write(f"Included extensions: {', '.join(self.extensions) if self.extensions else 'All'}\n")
        outfile.write(f"Excluded folders: {', '.join(self.exclude_folders)}\n")
        outfile.write("=" * 80 + "\n\n")

    def _write_file_header(self, outfile, filepath):
        """Writes file header information to output."""
        outfile.write("=" * 80 + "\n")
        outfile.write(f"File: {os.path.relpath(filepath, self.source_dir)}\n")
        if self.include_timestamp:
             timestamp = datetime.datetime.fromtimestamp(os.path.getmtime(filepath))
             outfile.write(f"Last Modified: {timestamp}\n")
        if self.include_file_size:
            try:
                size = os.path.getsize(filepath)
                outfile.write(f"Size: {size / 1024:.2f} KB\n")
            except OSError as e:
                logging.warning(f"Could not get size of {filepath} for header: {e}")
        outfile.write("=" * 80 + "\n\n")

    def _write_combination_summary(self, outfile, files_processed, total_size):
        """Writes the final summary of the combination to the output file."""
        outfile.write("=" * 80 + "\n")
        outfile.write(f"Total files processed: {files_processed}\n")
        outfile.write(f"Total size: {total_size / 1024 / 1024:.2f} MB\n")

    def combine_files(self):
       """Combines the files based on user preferences."""
       try:
            with open(self.output_file, 'w', encoding='utf-8') as outfile:
                self._write_summary(outfile)
                file_paths = []
                for dirpath, dirnames, filenames in os.walk(self.source_dir, followlinks=False):
                    dirnames[:] = [d for d in dirnames if d not in self.exclude_folders]
                    for filename in filenames:
                        filepath = os.path.join(dirpath, filename)
                        if self.should_process_file(filepath):
                            file_paths.append(filepath)

                with ThreadPoolExecutor(max_workers=self.num_worker_threads) as executor:
                    # Submit tasks and process results as they become available to avoid holding all in memory
                    futures = [executor.submit(self._process_file, filepath, outfile) for filepath in file_paths]
                    files_processed = 0
                    total_size = 0
                    for future in futures:
                        try:
                            processed, size = future.result()
                            files_processed += processed
                            total_size += size
                        except Exception as e:
                            logging.error(f"Error processing a file: {e}")

                self._write_combination_summary(outfile, files_processed, total_size)

            if self.create_zip_archive:
                self._create_zip_archive()

            logging.info(f"Combined {files_processed} files into {self.output_file}")
            logging.info(f"Total size: {total_size / 1024 / 1024:.2f} MB")
            print(f"\nCombined {files_processed} files into {self.output_file}")
            print(f"Total size: {total_size / 1024 / 1024:.2f} MB")

       except Exception as e:
            logging.error(f"An error occurred during file combination: {e}")
            print(f"An error occurred: {e}")

    def _create_zip_archive(self):
        """Creates a zip archive of the output file."""
        zip_filename = self.output_file.replace('.txt', '.zip')
        try:
            with zipfile.ZipFile(zip_filename, 'w', zipfile.ZIP_DEFLATED) as zipf:
                zipf.write(self.output_file, arcname=os.path.basename(self.output_file))
            logging.info(f"Created zip archive: {zip_filename}")
            print(f"Created zip archive: {zip_filename}")

        except Exception as e:
            logging.error(f"Error creating zip archive: {e}")
            print(f"Error creating zip archive: {e}")

def main():
    combiner = FileCombiner()
    combiner.get_user_preferences()
    combiner.combine_files()

if __name__ == "__main__":
    main()
