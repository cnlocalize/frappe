# Copyright (c) 2020, Frappe Technologies Pvt. Ltd. and Contributors

# imports - standard imports
import json
import os
import shlex
import subprocess
import sys
import unittest
import gzip
from glob import glob

# imports - module imports
import frappe
from frappe.utils import add_to_date, now
from frappe.utils.backups import fetch_latest_backups
import frappe.recorder


# TODO: check frappe.cli.coloured_output to set coloured output!

def supports_color():
	"""
	Returns True if the running system's terminal supports color, and False
	otherwise.
	"""
	plat = sys.platform
	supported_platform = plat != 'Pocket PC' and (plat != 'win32' or 'ANSICON' in os.environ)
	# isatty is not always implemented, #6223.
	is_a_tty = hasattr(sys.stdout, 'isatty') and sys.stdout.isatty()
	return supported_platform and is_a_tty


class color(dict):
	nc = '\033[0m'
	blue = '\033[94m'
	green = '\033[92m'
	yellow = '\033[93m'
	red = '\033[91m'
	silver = '\033[90m'

	def __getattr__(self, key):
		if supports_color():
			ret = self.get(key)
		else:
			ret = ""
		return ret


def clean(value):
	"""Strips and converts bytes to str

	Args:
		value ([type]): [description]

	Returns:
		[type]: [description]
	"""
	if isinstance(value, bytes):
		value = value.decode()
	if isinstance(value, str):
		value = value.strip()
	return value


def exists_in_backup(doctypes, file):
	"""Checks if the list of doctypes exist in the database.sql.gz file supplied

	Args:
		doctypes (list): List of DocTypes to be checked
		file (str): Path of the database file

	Returns:
		bool: True if all tables exist
	"""
	predicate = (
		'COPY public."tab{}"'
		if frappe.conf.db_type == "postgres"
		else "CREATE TABLE `tab{}`"
	)
	with gzip.open(file, "rb") as f:
		content = f.read().decode("utf8")
	return all([predicate.format(doctype).lower() in content.lower() for doctype in doctypes])

class BaseTestCommands(unittest.TestCase):
	def execute(self, command, kwargs=None):
		site = {"site": frappe.local.site}
		if kwargs:
			kwargs.update(site)
		else:
			kwargs = site
		self.command = " ".join(command.split()).format(**kwargs)
		print("{0}$ {1}{2}".format(color.silver, self.command, color.nc))
		command = shlex.split(self.command)
		self._proc = subprocess.run(command, stdout=subprocess.PIPE, stderr=subprocess.PIPE)
		self.stdout = clean(self._proc.stdout)
		self.stderr = clean(self._proc.stderr)
		self.returncode = clean(self._proc.returncode)

	def _formatMessage(self, msg, standardMsg):
		output = super(BaseTestCommands, self)._formatMessage(msg, standardMsg)
		cmd_execution_summary = "\n".join([
			"-" * 70,
			"Last Command Execution Summary:",
			"Command: {}".format(self.command) if self.command else "",
			"Standard Output: {}".format(self.stdout) if self.stdout else "",
			"Standard Error: {}".format(self.stderr) if self.stderr else "",
			"Return Code: {}".format(self.returncode) if self.returncode else "",
		]).strip()
		return "{}\n\n{}".format(output, cmd_execution_summary)

class TestCommands(BaseTestCommands):
	def test_execute(self):
		# test 1: execute a command expecting a numeric output
		self.execute("bench --site {site} execute frappe.db.get_database_size")
		self.assertEquals(self.returncode, 0)
		self.assertIsInstance(float(self.stdout), float)

		# test 2: execute a command expecting an errored output as local won't exist
		self.execute("bench --site {site} execute frappe.local.site")
		self.assertEquals(self.returncode, 1)
		self.assertIsNotNone(self.stderr)

		# test 3: execute a command with kwargs
		# Note:
		# terminal command has been escaped to avoid .format string replacement
		# The returned value has quotes which have been trimmed for the test
		self.execute("""bench --site {site} execute frappe.bold --kwargs '{{"text": "DocType"}}'""")
		self.assertEquals(self.returncode, 0)
		self.assertEquals(self.stdout[1:-1], frappe.bold(text='DocType'))

	def test_backup(self):
		backup = {
			"includes": {
				"includes": [
					"ToDo",
					"Note",
				]
			},
			"excludes": {
				"excludes": [
					"Activity Log",
					"Access Log",
					"Error Log"
				]
			}
		}
		home = os.path.expanduser("~")
		site_backup_path = frappe.utils.get_site_path("private", "backups")

		# test 1: take a backup
		before_backup = fetch_latest_backups()
		self.execute("bench --site {site} backup")
		after_backup = fetch_latest_backups()

		self.assertEquals(self.returncode, 0)
		self.assertIn("successfully completed", self.stdout)
		self.assertNotEqual(before_backup["database"], after_backup["database"])

		# test 2: take a backup with --with-files
		before_backup = after_backup.copy()
		self.execute("bench --site {site} backup --with-files")
		after_backup = fetch_latest_backups()

		self.assertEquals(self.returncode, 0)
		self.assertIn("successfully completed", self.stdout)
		self.assertIn("with files", self.stdout)
		self.assertNotEqual(before_backup, after_backup)
		self.assertIsNotNone(after_backup["public"])
		self.assertIsNotNone(after_backup["private"])

		# test 3: take a backup with --backup-path
		backup_path = os.path.join(home, "backups")
		self.execute("bench --site {site} backup --backup-path {backup_path}", {"backup_path": backup_path})

		self.assertEquals(self.returncode, 0)
		self.assertTrue(os.path.exists(backup_path))
		self.assertGreaterEqual(len(os.listdir(backup_path)), 2)

		# test 4: take a backup with --backup-path-db, --backup-path-files, --backup-path-private-files, --backup-path-conf
		kwargs = {
			key: os.path.join(home, key, value)
			for key, value in {
				"db_path": "database.sql.gz",
				"files_path": "public.tar",
				"private_path": "private.tar",
				"conf_path": "config.json"
			}.items()
		}

		self.execute("""bench
			--site {site} backup --with-files
			--backup-path-db {db_path}
			--backup-path-files {files_path}
			--backup-path-private-files {private_path}
			--backup-path-conf {conf_path}""", kwargs)

		self.assertEquals(self.returncode, 0)
		for path in kwargs.values():
			self.assertTrue(os.path.exists(path))

		# test 5: take a backup with --compress
		self.execute("bench --site {site} backup --with-files --compress")

		self.assertEquals(self.returncode, 0)

		compressed_files = glob(site_backup_path + "/*.tgz")
		self.assertGreater(len(compressed_files), 0)

		# test 6: take a backup with --verbose
		self.execute("bench --site {site} backup --verbose")
		self.assertEquals(self.returncode, 0)

		# test 7: take a backup with frappe.conf.backup.includes
		self.execute("bench --site {site} set-config backup '{includes}' --as-dict", {"includes": json.dumps(backup["includes"])})
		self.execute("bench --site {site} backup --verbose")
		self.assertEquals(self.returncode, 0)
		database = fetch_latest_backups()["database"]
		self.assertTrue(exists_in_backup(backup["includes"]["includes"], database))

		# test 8: take a backup with frappe.conf.backup.excludes
		self.execute("bench --site {site} set-config backup '{excludes}' --as-dict", {"excludes": json.dumps(backup["excludes"])})
		self.execute("bench --site {site} backup --verbose")
		self.assertEquals(self.returncode, 0)
		database = fetch_latest_backups()["database"]
		self.assertFalse(exists_in_backup(backup["excludes"]["excludes"], database))
		self.assertTrue(exists_in_backup(backup["includes"]["includes"], database))

		# test 9: take a backup with --include (with frappe.conf.excludes still set)
		self.execute("bench --site {site} backup --include '{include}'", {"include": ",".join(backup["includes"]["includes"])})
		self.assertEquals(self.returncode, 0)
		database = fetch_latest_backups()["database"]
		self.assertTrue(exists_in_backup(backup["includes"]["includes"], database))

		# test 10: take a backup with --exclude
		self.execute("bench --site {site} backup --exclude '{exclude}'", {"exclude": ",".join(backup["excludes"]["excludes"])})
		self.assertEquals(self.returncode, 0)
		database = fetch_latest_backups()["database"]
		self.assertFalse(exists_in_backup(backup["excludes"]["excludes"], database))

		# test 11: take a backup with --ignore-backup-conf
		self.execute("bench --site {site} backup --ignore-backup-conf")
		self.assertEquals(self.returncode, 0)
		database = fetch_latest_backups()["database"]
		self.assertTrue(exists_in_backup(backup["excludes"]["excludes"], database))

	def test_partial_restore(self):
		_now = now()
		for num in range(10):
			frappe.get_doc({
				"doctype": "ToDo",
				"date": add_to_date(_now, days=num),
				"description": frappe.mock("paragraph")
			}).insert()
		todo_count = frappe.db.count("ToDo")

		# check if todos exist, create a partial backup and see if the state is the same after restore
		self.assertIsNot(todo_count, 0)
		self.execute("bench --site {site} backup --only 'ToDo'")
		db_path = fetch_latest_backups(partial=True)["database"]
		self.assertTrue("partial" in db_path)

		frappe.db.sql_ddl("DROP TABLE IF EXISTS `tabToDo`")
		frappe.db.commit()

		self.execute("bench --site {site} partial-restore {path}", {"path": db_path})
		self.assertEquals(self.returncode, 0)
		frappe.db.commit()
		self.assertEquals(frappe.db.count("ToDo"), todo_count)

	def test_recorder(self):
		frappe.recorder.stop()

		self.execute("bench --site {site} start-recording")
		frappe.local.cache = {}
		self.assertEqual(frappe.recorder.status(), True)

		self.execute("bench --site {site} stop-recording")
		frappe.local.cache = {}
		self.assertEqual(frappe.recorder.status(), False)

	def test_remove_from_installed_apps(self):
		from frappe.installer import add_to_installed_apps
		app = "test_remove_app"
		add_to_installed_apps(app)

		# check: confirm that add_to_installed_apps added the app in the default
		self.execute("bench --site {site} list-apps")
		self.assertIn(app, self.stdout)

		# test 1: remove app from installed_apps global default
		self.execute("bench --site {site} remove-from-installed-apps {app}", {"app": app})
		self.execute("bench --site {site} list-apps")
		self.assertNotIn(app, self.stdout)
