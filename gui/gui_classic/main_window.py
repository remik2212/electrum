#!/usr/bin/env python
#
# Electrum - lightweight Bitcoin client
# Copyright (C) 2012 thomasv@gitorious
#
# This program is free software: you can redistribute it and/or modify
# it under the terms of the GNU General Public License as published by
# the Free Software Foundation, either version 3 of the License, or
# (at your option) any later version.
#
# This program is distributed in the hope that it will be useful,
# but WITHOUT ANY WARRANTY; without even the implied warranty of
# MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE. See the
# GNU General Public License for more details.
#
# You should have received a copy of the GNU General Public License
# along with this program. If not, see <http://www.gnu.org/licenses/>.

import sys, time, datetime, re, threading
from electrum.i18n import _, set_language
from electrum.util import print_error, print_msg
import os.path, json, ast, traceback
import shutil
import StringIO


try:
    import PyQt4
except:
    sys.exit("Error: Could not import PyQt4 on Linux systems, you may try 'sudo apt-get install python-qt4'")

from PyQt4.QtGui import *
from PyQt4.QtCore import *
import PyQt4.QtCore as QtCore

from electrum.bitcoin import MIN_RELAY_TX_FEE, is_valid

try:
    import icons_rc
except:
    sys.exit("Error: Could not import icons_rc.py, please generate it with: 'pyrcc4 icons.qrc -o gui/icons_rc.py'")

from electrum.wallet import format_satoshis
from electrum import Transaction
from electrum import mnemonic
from electrum import util, bitcoin, commands, Interface, Wallet
from electrum import SimpleConfig, Wallet, WalletStorage


from electrum import bmp, pyqrnative
import exchange_rate

from amountedit import AmountEdit
from network_dialog import NetworkDialog
from qrcodewidget import QRCodeWidget

from decimal import Decimal

import platform
import httplib
import socket
import webbrowser
import csv

if platform.system() == 'Windows':
    MONOSPACE_FONT = 'Lucida Console'
elif platform.system() == 'Darwin':
    MONOSPACE_FONT = 'Monaco'
else:
    MONOSPACE_FONT = 'monospace'

from electrum import ELECTRUM_VERSION
import re

from qt_util import *


        



class StatusBarButton(QPushButton):
    def __init__(self, icon, tooltip, func):
        QPushButton.__init__(self, icon, '')
        self.setToolTip(tooltip)
        self.setFlat(True)
        self.setMaximumWidth(25)
        self.clicked.connect(func)
        self.func = func
        self.setIconSize(QSize(25,25))

    def keyPressEvent(self, e):
        if e.key() == QtCore.Qt.Key_Return:
            apply(self.func,())










default_column_widths = { "history":[40,140,350,140], "contacts":[350,330], "receive":[[370], [370,200,130]] }

class ElectrumWindow(QMainWindow):
    def changeEvent(self, event):
        flags = self.windowFlags();
        if event and event.type() == QtCore.QEvent.WindowStateChange:
            if self.windowState() & QtCore.Qt.WindowMinimized:
                self.build_menu(True)
                # The only way to toggle the icon in the window managers taskbar is to use the Qt.Tooltip flag
                # The problem is that it somehow creates an (in)visible window that will stay active and prevent
                # Electrum from closing.
                # As for now I have no clue how to implement a proper 'hide to tray' functionality.
                # self.setWindowFlags(flags & ~Qt.ToolTip)
            elif event.oldState() & QtCore.Qt.WindowMinimized:
                self.build_menu(False)
                #self.setWindowFlags(flags | Qt.ToolTip)

    def build_menu(self, is_hidden = False):
        m = QMenu()
        if self.isMinimized():
            m.addAction(_("Show"), self.showNormal)
        else:
            m.addAction(_("Hide"), self.showMinimized)

        m.addSeparator()
        m.addAction(_("Exit Electrum"), self.close)
        self.tray.setContextMenu(m)

    def tray_activated(self, reason):
        if reason == QSystemTrayIcon.DoubleClick:
            self.showNormal()


    def __init__(self, config, network, go_lite):
        QMainWindow.__init__(self)

        self.config = config
        self.network = network
        self.go_lite = go_lite
        self.init_plugins()

        self._close_electrum = False
        self.lite = None
        self.current_account = self.config.get("current_account", None)

        self.icon = QIcon(':icons/electrum.png')
        self.tray = QSystemTrayIcon(self.icon, self)
        self.tray.setToolTip('Electrum')
        self.tray.activated.connect(self.tray_activated)

        self.build_menu()
        self.tray.show()
        self.create_status_bar()

        self.need_update = threading.Event()

        self.expert_mode   = config.get('classic_expert_mode', False)
        self.decimal_point = config.get('decimal_point', 8)
        self.num_zeros     = int(config.get('num_zeros',0))

        set_language(config.get('language'))

        self.funds_error = False
        self.completions = QStringListModel()

        self.tabs = tabs = QTabWidget(self)
        self.column_widths = self.config.get("column_widths", default_column_widths )
        tabs.addTab(self.create_history_tab(), _('History') )
        tabs.addTab(self.create_send_tab(), _('Send') )
        tabs.addTab(self.create_receive_tab(), _('Receive') )
        tabs.addTab(self.create_contacts_tab(), _('Contacts') )
        tabs.addTab(self.create_console_tab(), _('Console') )
        tabs.setMinimumSize(600, 400)
        tabs.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Expanding)
        self.setCentralWidget(tabs)

        g = self.config.get("winpos-qt",[100, 100, 840, 400])
        self.setGeometry(g[0], g[1], g[2], g[3])

        self.init_menubar()

        QShortcut(QKeySequence("Ctrl+W"), self, self.close)
        QShortcut(QKeySequence("Ctrl+R"), self, self.update_wallet)
        QShortcut(QKeySequence("Ctrl+Q"), self, self.close)
        QShortcut(QKeySequence("Ctrl+PgUp"), self, lambda: tabs.setCurrentIndex( (tabs.currentIndex() - 1 )%tabs.count() ))
        QShortcut(QKeySequence("Ctrl+PgDown"), self, lambda: tabs.setCurrentIndex( (tabs.currentIndex() + 1 )%tabs.count() ))
        
        self.connect(self, QtCore.SIGNAL('update_status'), self.update_status)
        self.connect(self, QtCore.SIGNAL('banner_signal'), lambda: self.console.showMessage(self.network.banner) )
        self.connect(self, QtCore.SIGNAL('transaction_signal'), lambda: self.notify_transactions() )

        self.history_list.setFocus(True)
        
        self.exchanger = exchange_rate.Exchanger(self)
        self.connect(self, SIGNAL("refresh_balance()"), self.update_wallet)

        # dark magic fix by flatfly; https://bitcointalk.org/index.php?topic=73651.msg959913#msg959913
        if platform.system() == 'Windows':
            n = 3 if self.wallet.seed else 2
            tabs.setCurrentIndex (n)
            tabs.setCurrentIndex (0)

        # plugins that need to change the GUI do it here
        self.run_hook('init')




    def load_wallet(self, wallet):
        import electrum
        self.wallet = wallet

        self.network.register_callback('updated', lambda: self.need_update.set())
        self.network.register_callback('banner', lambda: self.emit(QtCore.SIGNAL('banner_signal')))
        self.network.register_callback('disconnected', lambda: self.emit(QtCore.SIGNAL('update_status')))
        self.network.register_callback('disconnecting', lambda: self.emit(QtCore.SIGNAL('update_status')))
        self.network.register_callback('new_transaction', lambda: self.emit(QtCore.SIGNAL('transaction_signal')))
        title = 'Electrum ' + self.wallet.electrum_version + '  -  ' + self.wallet.storage.path
        if not self.wallet.seed: title += ' [%s]' % (_('seedless'))
        self.setWindowTitle( title )
        self.update_wallet()
        # set initial message
        self.console.showMessage(self.network.banner)
        # Once GUI has been initialized check if we want to announce something since the callback has been called before the GUI was initialized
        self.notify_transactions()

        # account selector
        accounts = self.wallet.get_account_names()
        self.account_selector.clear()
        if len(accounts) > 1:
            self.account_selector.addItems([_("All accounts")] + accounts.values())
            self.account_selector.setCurrentIndex(0)
            self.account_selector.show()
        else:
            self.account_selector.hide()

        self.new_account.setEnabled(self.wallet.seed_version>4)

        self.update_lock_icon()
        self.update_buttons_on_seed()
        self.update_console()

        self.run_hook('load_wallet')


    def select_wallet_file(self):
        wallet_folder = self.wallet.storage.path
        re.sub("(\/\w*.dat)$", "", wallet_folder)
        file_name = unicode( QFileDialog.getOpenFileName(self, "Select your wallet file", wallet_folder) )
        return file_name


    def open_wallet(self):

        filename = self.select_wallet_file()
        if not filename:
            return

        storage = WalletStorage({'wallet_path': filename})
        if not storage.file_exists:
            self.show_message("file not found "+ filename)
            return

        self.wallet.stop_threads()
        
        # create new wallet 
        wallet = Wallet(storage)
        wallet.start_threads(self.network)

        self.load_wallet(wallet)



    def backup_wallet(self):
        import shutil
        path = self.wallet.storage.path
        wallet_folder = os.path.dirname(path)
        new_filename, ok = QInputDialog.getText(self, _('Filename'), _('Current directory') + ': ' + wallet_folder + '\n' + _('Enter a filename for the copy of your wallet') + ':')
        new_filename = unicode(new_filename)
        if not ok or not new_filename:
            return

        new_path = os.path.join(wallet_folder, new_filename)
        if new_path != path:
            try:
                shutil.copy2(path, new_path)
                QMessageBox.information(None,"Wallet backup created", _("A copy of your wallet file was created in")+" '%s'" % str(new_path))
            except (IOError, os.error), reason:
                QMessageBox.critical(None,"Unable to create backup", _("Electrum was unable to copy your wallet file to the specified location.")+"\n" + str(reason))


    def new_wallet(self):
        import installwizard

        wallet_folder = os.path.dirname(self.wallet.storage.path)
        filename, ok = QInputDialog.getText(self, _('Filename'), _('Current directory') + ': ' + wallet_folder + '\n'+_('Enter a new file name') + ':')
        filename = unicode(filename)
        if not ok or not filename:
            return
        filename = os.path.join(wallet_folder, filename)

        storage = WalletStorage({'wallet_path': filename})
        assert not storage.file_exists

        wizard = installwizard.InstallWizard(self.config, self.network, storage)
        wallet = wizard.run()
        if wallet: 
            self.load_wallet(wallet)
        


    def init_menubar(self):
        menubar = QMenuBar()

        file_menu = menubar.addMenu(_("&File"))
        open_wallet_action = file_menu.addAction(_("&Open"))
        open_wallet_action.triggered.connect(self.open_wallet)

        new_wallet_action = file_menu.addAction(_("&Create/Restore"))
        new_wallet_action.triggered.connect(self.new_wallet)

        wallet_backup = file_menu.addAction(_("&Copy"))
        wallet_backup.triggered.connect(self.backup_wallet)

        quit_item = file_menu.addAction(_("&Close"))
        quit_item.triggered.connect(self.close)

        wallet_menu = menubar.addMenu(_("&Wallet"))

        # Settings / Preferences are all reserved keywords in OSX using this as work around
        preferences_name = _("Electrum preferences") if sys.platform == 'darwin' else _("Preferences")
        preferences_menu = wallet_menu.addAction(preferences_name)
        preferences_menu.triggered.connect(self.settings_dialog)

        wallet_menu.addSeparator()

        raw_transaction_menu = wallet_menu.addMenu(_("&Load raw transaction"))

        raw_transaction_file = raw_transaction_menu.addAction(_("&From file"))
        raw_transaction_file.triggered.connect(self.do_process_from_file)

        raw_transaction_text = raw_transaction_menu.addAction(_("&From text"))
        raw_transaction_text.triggered.connect(self.do_process_from_text)

        csv_transaction_menu = wallet_menu.addMenu(_("&Create transaction"))

        csv_transaction_file = csv_transaction_menu.addAction(_("&From CSV file"))
        csv_transaction_file.triggered.connect(self.do_process_from_csv_file)

        csv_transaction_text = csv_transaction_menu.addAction(_("&From CSV text"))
        csv_transaction_text.triggered.connect(self.do_process_from_csv_text)

        wallet_menu.addSeparator()

        show_menu = wallet_menu.addMenu(_("Show"))

        #if self.wallet.seed:
        show_seed = show_menu.addAction(_("&Seed"))
        show_seed.triggered.connect(self.show_seed_dialog)

        show_mpk = show_menu.addAction(_("&Master Public Key"))
        show_mpk.triggered.connect(self.show_master_public_key)

        wallet_menu.addSeparator()
        new_contact = wallet_menu.addAction(_("&New contact"))
        new_contact.triggered.connect(self.new_contact_dialog)

        self.new_account = wallet_menu.addAction(_("&New account"))
        self.new_account.triggered.connect(self.new_account_dialog)

        import_menu = menubar.addMenu(_("&Import"))
        in_labels = import_menu.addAction(_("&Labels"))
        in_labels.triggered.connect(self.do_import_labels)

        in_private_keys = import_menu.addAction(_("&Private keys"))
        in_private_keys.triggered.connect(self.do_import_privkey)

        export_menu = menubar.addMenu(_("&Export"))
        ex_private_keys = export_menu.addAction(_("&Private keys"))
        ex_private_keys.triggered.connect(self.do_export_privkeys)

        ex_history = export_menu.addAction(_("&History"))
        ex_history.triggered.connect(self.do_export_history)

        ex_labels = export_menu.addAction(_("&Labels"))
        ex_labels.triggered.connect(self.do_export_labels)

        help_menu = menubar.addMenu(_("&Help"))
        show_about = help_menu.addAction(_("&About"))
        show_about.triggered.connect(self.show_about)
        web_open = help_menu.addAction(_("&Official website")) 
        web_open.triggered.connect(lambda: webbrowser.open("http://electrum.org"))

        help_menu.addSeparator()
        doc_open = help_menu.addAction(_("&Documentation"))
        doc_open.triggered.connect(lambda: webbrowser.open("http://electrum.org/documentation.html"))
        report_bug = help_menu.addAction(_("&Report Bug"))
        report_bug.triggered.connect(self.show_report_bug)

        self.setMenuBar(menubar)

    def show_about(self):
        QMessageBox.about(self, "Electrum",
            _("Version")+" %s" % (self.wallet.electrum_version) + "\n\n" + _("Electrum's focus is speed, with low resource usage and simplifying Bitcoin. You do not need to perform regular backups, because your wallet can be recovered from a secret phrase that you can memorize or write on paper. Startup times are instant because it operates in conjunction with high-performance servers that handle the most complicated parts of the Bitcoin system."))

    def show_report_bug(self):
        QMessageBox.information(self, "Electrum - " + _("Reporting Bugs"),
            _("Please report any bugs as issues on github:")+" <a href=\"https://github.com/spesmilo/electrum/issues\">https://github.com/spesmilo/electrum/issues</a>")


    def notify_transactions(self):
        print_error("Notifying GUI")
        if len(self.network.interface.pending_transactions_for_notifications) > 0:
            # Combine the transactions if there are more then three
            tx_amount = len(self.network.interface.pending_transactions_for_notifications)
            if(tx_amount >= 3):
                total_amount = 0
                for tx in self.network.interface.pending_transactions_for_notifications:
                    is_relevant, is_mine, v, fee = self.wallet.get_tx_value(tx)
                    if(v > 0):
                        total_amount += v

                self.notify("%s new transactions received. Total amount received in the new transactions %s %s" \
                                % (tx_amount, self.format_amount(total_amount), self.base_unit()))

                self.network.interface.pending_transactions_for_notifications = []
            else:
              for tx in self.network.interface.pending_transactions_for_notifications:
                  if tx:
                      self.network.interface.pending_transactions_for_notifications.remove(tx)
                      is_relevant, is_mine, v, fee = self.wallet.get_tx_value(tx)
                      if(v > 0):
                          self.notify("New transaction received. %s %s" % (self.format_amount(v), self.base_unit()))

    def notify(self, message):
        self.tray.showMessage("Electrum", message, QSystemTrayIcon.Information, 20000)

    # plugins
    def init_plugins(self):
        import imp, pkgutil, __builtin__
        if __builtin__.use_local_modules:
            fp, pathname, description = imp.find_module('plugins')
            plugin_names = [name for a, name, b in pkgutil.iter_modules([pathname])]
            plugin_names = filter( lambda name: os.path.exists(os.path.join(pathname,name+'.py')), plugin_names)
            imp.load_module('electrum_plugins', fp, pathname, description)
            plugins = map(lambda name: imp.load_source('electrum_plugins.'+name, os.path.join(pathname,name+'.py')), plugin_names)
        else:
            import electrum_plugins
            plugin_names = [name for a, name, b in pkgutil.iter_modules(electrum_plugins.__path__)]
            plugins = [ __import__('electrum_plugins.'+name, fromlist=['electrum_plugins']) for name in plugin_names]

        self.plugins = []
        for name, p in zip(plugin_names, plugins):
            try:
                self.plugins.append( p.Plugin(self, name) )
            except:
                print_msg("Error:cannot initialize plugin",p)
                traceback.print_exc(file=sys.stdout)


    def run_hook(self, name, *args):
        for p in self.plugins:
            if not p.is_enabled():
                continue
            try:
                f = eval('p.'+name)
            except:
                continue
            try:
                apply(f, args)
            except:
                print_error("Plugin error")
                traceback.print_exc(file=sys.stdout)
                
        return



    def set_label(self, name, text = None):
        changed = False
        old_text = self.wallet.labels.get(name)
        if text:
            if old_text != text:
                self.wallet.labels[name] = text
                self.wallet.storage.put('labels', self.wallet.labels)
                changed = True
        else:
            if old_text:
                self.wallet.labels.pop(name)
                changed = True
        self.run_hook('set_label', name, text, changed)
        return changed


    # custom wrappers for getOpenFileName and getSaveFileName, that remember the path selected by the user
    def getOpenFileName(self, title, filter = ""):
        directory = self.config.get('io_dir', os.path.expanduser('~'))
        fileName = unicode( QFileDialog.getOpenFileName(self, title, directory, filter) )
        if fileName and directory != os.path.dirname(fileName):
            self.config.set_key('io_dir', os.path.dirname(fileName), True)
        return fileName

    def getSaveFileName(self, title, filename, filter = ""):
        directory = self.config.get('io_dir', os.path.expanduser('~'))
        path = os.path.join( directory, filename )
        fileName = unicode( QFileDialog.getSaveFileName(self, title, path, filter) )
        if fileName and directory != os.path.dirname(fileName):
            self.config.set_key('io_dir', os.path.dirname(fileName), True)
        return fileName

    def close(self):
        QMainWindow.close(self)
        self.run_hook('close_main_window')

    def connect_slots(self, sender):
        self.connect(sender, QtCore.SIGNAL('timersignal'), self.timer_actions)
        self.previous_payto_e=''

    def timer_actions(self):
        if self.need_update.is_set():
            self.update_wallet()
            self.need_update.clear()
        self.run_hook('timer_actions')
    
    def format_amount(self, x, is_diff=False, whitespaces=False):
        return format_satoshis(x, is_diff, self.num_zeros, self.decimal_point, whitespaces)

    def read_amount(self, x):
        if x in['.', '']: return None
        p = pow(10, self.decimal_point)
        return int( p * Decimal(x) )

    def base_unit(self):
        assert self.decimal_point in [5,8]
        return "BTC" if self.decimal_point == 8 else "mBTC"

    def update_status(self):
        if self.network.interface and self.network.interface.is_connected:
            if not self.wallet.up_to_date:
                text = _("Synchronizing...")
                icon = QIcon(":icons/status_waiting.png")
            else:
                c, u = self.wallet.get_account_balance(self.current_account)
                text =  _( "Balance" ) + ": %s "%( self.format_amount(c) ) + self.base_unit()
                if u: text +=  " [%s unconfirmed]"%( self.format_amount(u,True).strip() )
                text += self.create_quote_text(Decimal(c+u)/100000000)
                self.tray.setToolTip(text)
                icon = QIcon(":icons/status_connected.png")
        else:
            text = _("Not connected")
            icon = QIcon(":icons/status_disconnected.png")

        self.balance_label.setText(text)
        self.status_button.setIcon( icon )

    def update_wallet(self):
        self.update_status()
        if self.wallet.up_to_date or not self.network.interface.is_connected:
            self.update_history_tab()
            self.update_receive_tab()
            self.update_contacts_tab()
            self.update_completions()


    def create_quote_text(self, btc_balance):
        quote_currency = self.config.get("currency", "None")
        quote_balance = self.exchanger.exchange(btc_balance, quote_currency)
        if quote_balance is None:
            quote_text = ""
        else:
            quote_text = "  (%.2f %s)" % (quote_balance, quote_currency)
        return quote_text
        
    def create_history_tab(self):
        self.history_list = l = MyTreeWidget(self)
        l.setColumnCount(5)
        for i,width in enumerate(self.column_widths['history']):
            l.setColumnWidth(i, width)
        l.setHeaderLabels( [ '', _('Date'), _('Description') , _('Amount'), _('Balance')] )
        self.connect(l, SIGNAL('itemDoubleClicked(QTreeWidgetItem*, int)'), self.tx_label_clicked)
        self.connect(l, SIGNAL('itemChanged(QTreeWidgetItem*, int)'), self.tx_label_changed)

        l.customContextMenuRequested.connect(self.create_history_menu)
        return l


    def create_history_menu(self, position):
        self.history_list.selectedIndexes() 
        item = self.history_list.currentItem()
        if not item: return
        tx_hash = str(item.data(0, Qt.UserRole).toString())
        if not tx_hash: return
        menu = QMenu()
        menu.addAction(_("Copy ID to Clipboard"), lambda: self.app.clipboard().setText(tx_hash))
        menu.addAction(_("Details"), lambda: self.show_transaction(self.wallet.transactions.get(tx_hash)))
        menu.addAction(_("Edit description"), lambda: self.tx_label_clicked(item,2))
        menu.exec_(self.contacts_list.viewport().mapToGlobal(position))


    def show_transaction(self, tx):
        import transaction_dialog
        d = transaction_dialog.TxDialog(tx, self)
        d.exec_()

    def tx_label_clicked(self, item, column):
        if column==2 and item.isSelected():
            self.is_edit=True
            item.setFlags(Qt.ItemIsEditable|Qt.ItemIsSelectable | Qt.ItemIsUserCheckable | Qt.ItemIsEnabled | Qt.ItemIsDragEnabled)
            self.history_list.editItem( item, column )
            item.setFlags(Qt.ItemIsSelectable | Qt.ItemIsUserCheckable | Qt.ItemIsEnabled | Qt.ItemIsDragEnabled)
            self.is_edit=False

    def tx_label_changed(self, item, column):
        if self.is_edit: 
            return
        self.is_edit=True
        tx_hash = str(item.data(0, Qt.UserRole).toString())
        tx = self.wallet.transactions.get(tx_hash)
        text = unicode( item.text(2) )
        self.set_label(tx_hash, text) 
        if text: 
            item.setForeground(2, QBrush(QColor('black')))
        else:
            text = self.wallet.get_default_label(tx_hash)
            item.setText(2, text)
            item.setForeground(2, QBrush(QColor('gray')))
        self.is_edit=False


    def edit_label(self, is_recv):
        l = self.receive_list if is_recv else self.contacts_list
        item = l.currentItem()
        item.setFlags(Qt.ItemIsEditable|Qt.ItemIsSelectable | Qt.ItemIsUserCheckable | Qt.ItemIsEnabled | Qt.ItemIsDragEnabled)
        l.editItem( item, 1 )
        item.setFlags(Qt.ItemIsSelectable | Qt.ItemIsUserCheckable | Qt.ItemIsEnabled | Qt.ItemIsDragEnabled)



    def address_label_clicked(self, item, column, l, column_addr, column_label):
        if column == column_label and item.isSelected():
            is_editable = item.data(0, 32).toBool()
            if not is_editable:
                return
            addr = unicode( item.text(column_addr) )
            label = unicode( item.text(column_label) )
            item.setFlags(Qt.ItemIsEditable|Qt.ItemIsSelectable | Qt.ItemIsUserCheckable | Qt.ItemIsEnabled | Qt.ItemIsDragEnabled)
            l.editItem( item, column )
            item.setFlags(Qt.ItemIsSelectable | Qt.ItemIsUserCheckable | Qt.ItemIsEnabled | Qt.ItemIsDragEnabled)


    def address_label_changed(self, item, column, l, column_addr, column_label):
        if column == column_label:
            addr = unicode( item.text(column_addr) )
            text = unicode( item.text(column_label) )
            is_editable = item.data(0, 32).toBool()
            if not is_editable:
                return

            changed = self.set_label(addr, text)
            if changed:
                self.update_history_tab()
                self.update_completions()
                
            self.current_item_changed(item)

        self.run_hook('item_changed', item, column)


    def current_item_changed(self, a):
        self.run_hook('current_item_changed', a)



    def update_history_tab(self):

        self.history_list.clear()
        for item in self.wallet.get_tx_history(self.current_account):
            tx_hash, conf, is_mine, value, fee, balance, timestamp = item
            if conf > 0:
                try:
                    time_str = datetime.datetime.fromtimestamp( timestamp).isoformat(' ')[:-3]
                except:
                    time_str = "unknown"

            if conf == -1:
                time_str = 'unverified'
                icon = QIcon(":icons/unconfirmed.png")
            elif conf == 0:
                time_str = 'pending'
                icon = QIcon(":icons/unconfirmed.png")
            elif conf < 6:
                icon = QIcon(":icons/clock%d.png"%conf)
            else:
                icon = QIcon(":icons/confirmed.png")

            if value is not None:
                v_str = self.format_amount(value, True, whitespaces=True)
            else:
                v_str = '--'

            balance_str = self.format_amount(balance, whitespaces=True)
            
            if tx_hash:
                label, is_default_label = self.wallet.get_label(tx_hash)
            else:
                label = _('Pruned transaction outputs')
                is_default_label = False

            item = QTreeWidgetItem( [ '', time_str, label, v_str, balance_str] )
            item.setFont(2, QFont(MONOSPACE_FONT))
            item.setFont(3, QFont(MONOSPACE_FONT))
            item.setFont(4, QFont(MONOSPACE_FONT))
            if value < 0:
                item.setForeground(3, QBrush(QColor("#BC1E1E")))
            if tx_hash:
                item.setData(0, Qt.UserRole, tx_hash)
                item.setToolTip(0, "%d %s\nTxId:%s" % (conf, _('Confirmations'), tx_hash) )
            if is_default_label:
                item.setForeground(2, QBrush(QColor('grey')))

            item.setIcon(0, icon)
            self.history_list.insertTopLevelItem(0,item)
            

        self.history_list.setCurrentItem(self.history_list.topLevelItem(0))


    def create_send_tab(self):
        w = QWidget()

        grid = QGridLayout()
        grid.setSpacing(8)
        grid.setColumnMinimumWidth(3,300)
        grid.setColumnStretch(5,1)


        self.payto_e = QLineEdit()
        grid.addWidget(QLabel(_('Pay to')), 1, 0)
        grid.addWidget(self.payto_e, 1, 1, 1, 3)
            
        grid.addWidget(HelpButton(_('Recipient of the funds.') + '\n\n' + _('You may enter a Bitcoin address, a label from your list of contacts (a list of completions will be proposed), or an alias (email-like address that forwards to a Bitcoin address)')), 1, 4)

        completer = QCompleter()
        completer.setCaseSensitivity(False)
        self.payto_e.setCompleter(completer)
        completer.setModel(self.completions)

        self.message_e = QLineEdit()
        grid.addWidget(QLabel(_('Description')), 2, 0)
        grid.addWidget(self.message_e, 2, 1, 1, 3)
        grid.addWidget(HelpButton(_('Description of the transaction (not mandatory).') + '\n\n' + _('The description is not sent to the recipient of the funds. It is stored in your wallet file, and displayed in the \'History\' tab.')), 2, 4)

        self.amount_e = AmountEdit(self.base_unit)
        grid.addWidget(QLabel(_('Amount')), 3, 0)
        grid.addWidget(self.amount_e, 3, 1, 1, 2)
        grid.addWidget(HelpButton(
                _('Amount to be sent.') + '\n\n' \
                    + _('The amount will be displayed in red if you do not have enough funds in your wallet. Note that if you have frozen some of your addresses, the available funds will be lower than your total balance.') \
                    + '\n\n' + _('Keyboard shortcut: type "!" to send all your coins.')), 3, 3)
        
        self.fee_e = AmountEdit(self.base_unit)
        grid.addWidget(QLabel(_('Fee')), 4, 0)
        grid.addWidget(self.fee_e, 4, 1, 1, 2) 
        grid.addWidget(HelpButton(
                _('Bitcoin transactions are in general not free. A transaction fee is paid by the sender of the funds.') + '\n\n'\
                    + _('The amount of fee can be decided freely by the sender. However, transactions with low fees take more time to be processed.') + '\n\n'\
                    + _('A suggested fee is automatically added to this field. You may override it. The suggested fee increases with the size of the transaction.')), 4, 3)


        self.send_button = EnterButton(_("Send"), self.do_send)
        grid.addWidget(self.send_button, 6, 1)

        b = EnterButton(_("Clear"),self.do_clear)
        grid.addWidget(b, 6, 2)

        self.payto_sig = QLabel('')
        grid.addWidget(self.payto_sig, 7, 0, 1, 4)

        QShortcut(QKeySequence("Up"), w, w.focusPreviousChild)
        QShortcut(QKeySequence("Down"), w, w.focusNextChild)
        w.setLayout(grid) 

        w2 = QWidget()
        vbox = QVBoxLayout()
        vbox.addWidget(w)
        vbox.addStretch(1)
        w2.setLayout(vbox)

        def entry_changed( is_fee ):
            self.funds_error = False

            if self.amount_e.is_shortcut:
                self.amount_e.is_shortcut = False
                c, u = self.wallet.get_account_balance(self.current_account)
                inputs, total, fee = self.wallet.choose_tx_inputs( c + u, 0, self.current_account)
                fee = self.wallet.estimated_fee(inputs)
                amount = c + u - fee
                self.amount_e.setText( self.format_amount(amount) )
                self.fee_e.setText( self.format_amount( fee ) )
                return
                
            amount = self.read_amount(str(self.amount_e.text()))
            fee = self.read_amount(str(self.fee_e.text()))

            if not is_fee: fee = None
            if amount is None:
                return
            inputs, total, fee = self.wallet.choose_tx_inputs( amount, fee, self.current_account )
            if not is_fee:
                self.fee_e.setText( self.format_amount( fee ) )
            if inputs:
                palette = QPalette()
                palette.setColor(self.amount_e.foregroundRole(), QColor('black'))
                text = ""
            else:
                palette = QPalette()
                palette.setColor(self.amount_e.foregroundRole(), QColor('red'))
                self.funds_error = True
                text = _( "Not enough funds" )
                c, u = self.wallet.get_frozen_balance()
                if c+u: text += ' (' + self.format_amount(c+u).strip() + self.base_unit() + ' ' +_("are frozen") + ')'

            self.statusBar().showMessage(text)
            self.amount_e.setPalette(palette)
            self.fee_e.setPalette(palette)

        self.amount_e.textChanged.connect(lambda: entry_changed(False) )
        self.fee_e.textChanged.connect(lambda: entry_changed(True) )

        self.run_hook('create_send_tab', grid)
        return w2


    def update_completions(self):
        l = []
        for addr,label in self.wallet.labels.items():
            if addr in self.wallet.addressbook:
                l.append( label + '  <' + addr + '>')

        self.run_hook('update_completions', l)
        self.completions.setStringList(l)


    def protected(func):
        return lambda s, *args: s.do_protect(func, args)


    def do_send(self):

        label = unicode( self.message_e.text() )
        r = unicode( self.payto_e.text() )
        r = r.strip()

        # label or alias, with address in brackets
        m = re.match('(.*?)\s*\<([1-9A-HJ-NP-Za-km-z]{26,})\>', r)
        to_address = m.group(2) if m else r

        if not is_valid(to_address):
            QMessageBox.warning(self, _('Error'), _('Invalid Bitcoin Address') + ':\n' + to_address, _('OK'))
            return

        try:
            amount = self.read_amount(unicode( self.amount_e.text()))
        except:
            QMessageBox.warning(self, _('Error'), _('Invalid Amount'), _('OK'))
            return
        try:
            fee = self.read_amount(unicode( self.fee_e.text()))
        except:
            QMessageBox.warning(self, _('Error'), _('Invalid Fee'), _('OK'))
            return

        confirm_amount = self.config.get('confirm_amount', 100000000)
        if amount >= confirm_amount:
            if not self.question("send %s to %s?"%(self.format_amount(amount) + ' '+ self.base_unit(), to_address)):
                return

        self.send_tx(to_address, amount, fee, label)


    @protected
    def send_tx(self, to_address, amount, fee, label, password):

        try:
            tx = self.wallet.mktx( [(to_address, amount)], password, fee, account=self.current_account)
        except BaseException, e:
            traceback.print_exc(file=sys.stdout)
            self.show_message(str(e))
            return

        if tx.requires_fee(self.wallet.verifier) and fee < MIN_RELAY_TX_FEE:
            QMessageBox.warning(self, _('Error'), _("This transaction requires a higher fee, or it will not be propagated by the network."), _('OK'))
            return

        self.run_hook('send_tx', tx)

        if label: 
            self.set_label(tx.hash(), label)

        if tx.is_complete:
            h = self.wallet.send_tx(tx)
            waiting_dialog(lambda: False if self.wallet.tx_event.isSet() else _("Please wait..."))
            status, msg = self.wallet.receive_tx( h )
            if status:
                QMessageBox.information(self, '', _('Payment sent.')+'\n'+msg, _('OK'))
                self.do_clear()
                self.update_contacts_tab()
            else:
                QMessageBox.warning(self, _('Error'), msg, _('OK'))
        else:
            filename = label + '.txn' if label else 'unsigned_%s.txn' % (time.mktime(time.gmtime()))
            try:
                fileName = self.getSaveFileName(_("Select a transaction filename"), filename, "*.txn")
                with open(fileName,'w') as f:
                    f.write(json.dumps(tx.as_dict(),indent=4) + '\n')
                QMessageBox.information(self, _('Unsigned transaction created'), _("Unsigned transaction was saved to file:") + " " +fileName, _('OK'))
            except:
                QMessageBox.warning(self, _('Error'), _('Could not write transaction to file'), _('OK'))

        # add recipient to addressbook
        if to_address not in self.wallet.addressbook and not self.wallet.is_mine(to_address):
            self.wallet.addressbook.append(to_address)




    def set_url(self, url):
        address, amount, label, message, signature, identity, url = util.parse_url(url)
        if self.base_unit() == 'mBTC': amount = str( 1000* Decimal(amount))

        if label and self.wallet.labels.get(address) != label:
            if self.question('Give label "%s" to address %s ?'%(label,address)):
                if address not in self.wallet.addressbook and not self.wallet.is_mine(address):
                    self.wallet.addressbook.append(address)
                self.set_label(address, label)

        self.run_hook('set_url', url, self.show_message, self.question)

        self.tabs.setCurrentIndex(1)
        label = self.wallet.labels.get(address)
        m_addr = label + '  <'+ address +'>' if label else address
        self.payto_e.setText(m_addr)

        self.message_e.setText(message)
        self.amount_e.setText(amount)
        if identity:
            self.set_frozen(self.payto_e,True)
            self.set_frozen(self.amount_e,True)
            self.set_frozen(self.message_e,True)
            self.payto_sig.setText( '      The bitcoin URI was signed by ' + identity )
        else:
            self.payto_sig.setVisible(False)

    def do_clear(self):
        self.payto_sig.setVisible(False)
        for e in [self.payto_e, self.message_e, self.amount_e, self.fee_e]:
            e.setText('')
            self.set_frozen(e,False)
        self.update_status()

    def set_frozen(self,entry,frozen):
        if frozen:
            entry.setReadOnly(True)
            entry.setFrame(False)
            palette = QPalette()
            palette.setColor(entry.backgroundRole(), QColor('lightgray'))
            entry.setPalette(palette)
        else:
            entry.setReadOnly(False)
            entry.setFrame(True)
            palette = QPalette()
            palette.setColor(entry.backgroundRole(), QColor('white'))
            entry.setPalette(palette)


    def toggle_freeze(self,addr):
        if not addr: return
        if addr in self.wallet.frozen_addresses:
            self.wallet.unfreeze(addr)
        else:
            self.wallet.freeze(addr)
        self.update_receive_tab()

    def toggle_priority(self,addr):
        if not addr: return
        if addr in self.wallet.prioritized_addresses:
            self.wallet.unprioritize(addr)
        else:
            self.wallet.prioritize(addr)
        self.update_receive_tab()


    def create_list_tab(self, headers):
        "generic tab creation method"
        l = MyTreeWidget(self)
        l.setColumnCount( len(headers) )
        l.setHeaderLabels( headers )

        w = QWidget()
        vbox = QVBoxLayout()
        w.setLayout(vbox)

        vbox.setMargin(0)
        vbox.setSpacing(0)
        vbox.addWidget(l)
        buttons = QWidget()
        vbox.addWidget(buttons)

        hbox = QHBoxLayout()
        hbox.setMargin(0)
        hbox.setSpacing(0)
        buttons.setLayout(hbox)

        return l,w,hbox


    def create_receive_tab(self):
        l,w,hbox = self.create_list_tab([ _('Address'), _('Label'), _('Balance'), _('Tx')])
        l.setContextMenuPolicy(Qt.CustomContextMenu)
        l.customContextMenuRequested.connect(self.create_receive_menu)
        self.connect(l, SIGNAL('itemDoubleClicked(QTreeWidgetItem*, int)'), lambda a, b: self.address_label_clicked(a,b,l,0,1))
        self.connect(l, SIGNAL('itemChanged(QTreeWidgetItem*, int)'), lambda a,b: self.address_label_changed(a,b,l,0,1))
        self.connect(l, SIGNAL('currentItemChanged(QTreeWidgetItem*, QTreeWidgetItem*)'), lambda a,b: self.current_item_changed(a))
        self.receive_list = l
        self.receive_buttons_hbox = hbox
        hbox.addStretch(1)
        return w


    def receive_tab_set_mode(self, i):
        self.save_column_widths()
        self.expert_mode = (i == 1)
        self.config.set_key('classic_expert_mode', self.expert_mode, True)
        self.update_receive_tab()


    def save_column_widths(self):
        if not self.expert_mode:
            widths = [ self.receive_list.columnWidth(0) ]
        else:
            widths = []
            for i in range(self.receive_list.columnCount() -1):
                widths.append(self.receive_list.columnWidth(i))
        self.column_widths["receive"][self.expert_mode] = widths
        
        self.column_widths["history"] = []
        for i in range(self.history_list.columnCount() - 1):
            self.column_widths["history"].append(self.history_list.columnWidth(i))

        self.column_widths["contacts"] = []
        for i in range(self.contacts_list.columnCount() - 1):
            self.column_widths["contacts"].append(self.contacts_list.columnWidth(i))

        self.config.set_key("column_widths", self.column_widths, True)


    def create_contacts_tab(self):
        l,w,hbox = self.create_list_tab([_('Address'), _('Label'), _('Tx')])
        l.setContextMenuPolicy(Qt.CustomContextMenu)
        l.customContextMenuRequested.connect(self.create_contact_menu)
        for i,width in enumerate(self.column_widths['contacts']):
            l.setColumnWidth(i, width)

        self.connect(l, SIGNAL('itemDoubleClicked(QTreeWidgetItem*, int)'), lambda a, b: self.address_label_clicked(a,b,l,0,1))
        self.connect(l, SIGNAL('itemChanged(QTreeWidgetItem*, int)'), lambda a,b: self.address_label_changed(a,b,l,0,1))
        self.contacts_list = l
        self.contacts_buttons_hbox = hbox
        hbox.addStretch(1)
        return w


    def delete_imported_key(self, addr):
        if self.question(_("Do you want to remove")+" %s "%addr +_("from your wallet?")):
            self.wallet.delete_imported_key(addr)
            self.update_receive_tab()
            self.update_history_tab()


    def create_receive_menu(self, position):
        # fixme: this function apparently has a side effect.
        # if it is not called the menu pops up several times
        #self.receive_list.selectedIndexes() 

        item = self.receive_list.itemAt(position)
        if not item: return
        addr = unicode(item.text(0))
        if not is_valid(addr): 
            item.setExpanded(not item.isExpanded())
            return 
        menu = QMenu()
        menu.addAction(_("Copy to clipboard"), lambda: self.app.clipboard().setText(addr))
        menu.addAction(_("QR code"), lambda: self.show_qrcode("bitcoin:" + addr, _("Address")) )
        menu.addAction(_("Edit label"), lambda: self.edit_label(True))
        menu.addAction(_("Private key"), lambda: self.show_private_key(addr))
        menu.addAction(_("Sign message"), lambda: self.sign_message(addr))
        if addr in self.wallet.imported_keys:
            menu.addAction(_("Remove from wallet"), lambda: self.delete_imported_key(addr))

        if self.expert_mode:
            t = _("Unfreeze") if addr in self.wallet.frozen_addresses else _("Freeze")
            menu.addAction(t, lambda: self.toggle_freeze(addr))
            t = _("Unprioritize") if addr in self.wallet.prioritized_addresses else _("Prioritize")
            menu.addAction(t, lambda: self.toggle_priority(addr))
            
        self.run_hook('receive_menu', menu)
        menu.exec_(self.receive_list.viewport().mapToGlobal(position))


    def payto(self, addr):
        if not addr: return
        label = self.wallet.labels.get(addr)
        m_addr = label + '  <' + addr + '>' if label else addr
        self.tabs.setCurrentIndex(1)
        self.payto_e.setText(m_addr)
        self.amount_e.setFocus()


    def delete_contact(self, x):
        if self.question(_("Do you want to remove")+" %s "%x +_("from your list of contacts?")):
            self.wallet.delete_contact(x)
            self.set_label(x, None)
            self.update_history_tab()
            self.update_contacts_tab()
            self.update_completions()


    def create_contact_menu(self, position):
        item = self.contacts_list.itemAt(position)
        if not item: return
        addr = unicode(item.text(0))
        label = unicode(item.text(1))
        is_editable = item.data(0,32).toBool()
        payto_addr = item.data(0,33).toString()
        menu = QMenu()
        menu.addAction(_("Copy to Clipboard"), lambda: self.app.clipboard().setText(addr))
        menu.addAction(_("Pay to"), lambda: self.payto(payto_addr))
        menu.addAction(_("QR code"), lambda: self.show_qrcode("bitcoin:" + addr, _("Address")))
        if is_editable:
            menu.addAction(_("Edit label"), lambda: self.edit_label(False))
            menu.addAction(_("Delete"), lambda: self.delete_contact(addr))

        self.run_hook('create_contact_menu', menu, item)
        menu.exec_(self.contacts_list.viewport().mapToGlobal(position))


    def update_receive_item(self, item):
        item.setFont(0, QFont(MONOSPACE_FONT))
        address = str(item.data(0,0).toString())
        label = self.wallet.labels.get(address,'')
        item.setData(1,0,label)
        item.setData(0,32, True) # is editable

        self.run_hook('update_receive_item', address, item)
                
        c, u = self.wallet.get_addr_balance(address)
        balance = self.format_amount(c + u)
        item.setData(2,0,balance)

        if self.expert_mode:
            if address in self.wallet.frozen_addresses: 
                item.setBackgroundColor(0, QColor('lightblue'))
            elif address in self.wallet.prioritized_addresses: 
                item.setBackgroundColor(0, QColor('lightgreen'))
        

    def update_receive_tab(self):
        l = self.receive_list
        
        l.clear()
        l.setColumnHidden(2, not self.expert_mode)
        l.setColumnHidden(3, not self.expert_mode)
        for i,width in enumerate(self.column_widths['receive'][self.expert_mode]):
            l.setColumnWidth(i, width)

        if self.current_account is None:
            account_items = self.wallet.accounts.items()
        elif self.current_account != -1:
            account_items = [(self.current_account, self.wallet.accounts.get(self.current_account))]
        else:
            account_items = []

        for k, account in account_items:
            name = self.wallet.get_account_name(k)
            c,u = self.wallet.get_account_balance(k)
            account_item = QTreeWidgetItem( [ name, '', self.format_amount(c+u), ''] )
            l.addTopLevelItem(account_item)
            account_item.setExpanded(True)
            
            for is_change in ([0,1] if self.expert_mode else [0]):
                if self.expert_mode:
                    name = "Receiving" if not is_change else "Change"
                    seq_item = QTreeWidgetItem( [ name, '', '', '', ''] )
                    account_item.addChild(seq_item)
                    if not is_change: seq_item.setExpanded(True)
                else:
                    seq_item = account_item
                is_red = False
                gap = 0

                for address in account.get_addresses(is_change):
                    h = self.wallet.history.get(address,[])
            
                    if h == []:
                        gap += 1
                        if gap > self.wallet.gap_limit:
                            is_red = True
                    else:
                        gap = 0

                    num_tx = '*' if h == ['*'] else "%d"%len(h)
                    item = QTreeWidgetItem( [ address, '', '', num_tx] )
                    self.update_receive_item(item)
                    if is_red:
                        item.setBackgroundColor(1, QColor('red'))
                    seq_item.addChild(item)


        if self.wallet.imported_keys and (self.current_account is None or self.current_account == -1):
            c,u = self.wallet.get_imported_balance()
            account_item = QTreeWidgetItem( [ _('Imported'), '', self.format_amount(c+u), ''] )
            l.addTopLevelItem(account_item)
            account_item.setExpanded(True)
            for address in self.wallet.imported_keys.keys():
                item = QTreeWidgetItem( [ address, '', '', ''] )
                self.update_receive_item(item)
                account_item.addChild(item)
                

        # we use column 1 because column 0 may be hidden
        l.setCurrentItem(l.topLevelItem(0),1)


    def update_contacts_tab(self):
        l = self.contacts_list
        l.clear()

        for address in self.wallet.addressbook:
            label = self.wallet.labels.get(address,'')
            n = self.wallet.get_num_tx(address)
            item = QTreeWidgetItem( [ address, label, "%d"%n] )
            item.setFont(0, QFont(MONOSPACE_FONT))
            # 32 = label can be edited (bool)
            item.setData(0,32, True)
            # 33 = payto string
            item.setData(0,33, address)
            l.addTopLevelItem(item)

        self.run_hook('update_contacts_tab', l)
        l.setCurrentItem(l.topLevelItem(0))



    def create_console_tab(self):
        from qt_console import Console
        self.console = console = Console()
        return console


    def update_console(self):
        console = self.console
        console.history = self.config.get("console-history",[])
        console.history_index = len(console.history)

        console.updateNamespace({'wallet' : self.wallet, 'network' : self.network, 'gui':self})
        console.updateNamespace({'util' : util, 'bitcoin':bitcoin})

        c = commands.Commands(self.wallet, self.network, lambda: self.console.set_json(True))
        methods = {}
        def mkfunc(f, method):
            return lambda *args: apply( f, (method, args, self.password_dialog ))
        for m in dir(c):
            if m[0]=='_' or m=='wallet' or m == 'interface': continue
            methods[m] = mkfunc(c._run, m)
            
        console.updateNamespace(methods)


    def change_account(self,s):
        if s == _("All accounts"):
            self.current_account = None
        else:
            accounts = self.wallet.get_account_names()
            for k, v in accounts.items():
                if v == s:
                    self.current_account = k
        self.update_history_tab()
        self.update_status()
        self.update_receive_tab()

    def create_status_bar(self):

        sb = QStatusBar()
        sb.setFixedHeight(35)
        qtVersion = qVersion()

        self.balance_label = QLabel("")
        sb.addWidget(self.balance_label)

        from version_getter import UpdateLabel
        self.updatelabel = UpdateLabel(self.config, sb)

        self.account_selector = QComboBox()
        self.connect(self.account_selector,SIGNAL("activated(QString)"),self.change_account) 
        sb.addPermanentWidget(self.account_selector)

        if (int(qtVersion[0]) >= 4 and int(qtVersion[2]) >= 7):
            sb.addPermanentWidget( StatusBarButton( QIcon(":icons/switchgui.png"), _("Switch to Lite Mode"), self.go_lite ) )

        self.lock_icon = QIcon()
        self.password_button = StatusBarButton( self.lock_icon, _("Password"), self.change_password_dialog )
        sb.addPermanentWidget( self.password_button )
            
        sb.addPermanentWidget( StatusBarButton( QIcon(":icons/preferences.png"), _("Preferences"), self.settings_dialog ) )
        self.seed_button = StatusBarButton( QIcon(":icons/seed.png"), _("Seed"), self.show_seed_dialog ) 
        sb.addPermanentWidget( self.seed_button )
        self.status_button = StatusBarButton( QIcon(":icons/status_disconnected.png"), _("Network"), self.run_network_dialog ) 
        sb.addPermanentWidget( self.status_button )

        self.run_hook('create_status_bar', (sb,))

        self.setStatusBar(sb)


    def update_lock_icon(self):
        icon = QIcon(":icons/lock.png") if self.wallet.use_encryption else QIcon(":icons/unlock.png")
        self.password_button.setIcon( icon )


    def update_buttons_on_seed(self):
        if self.wallet.seed:
           self.seed_button.show()
           self.password_button.show()
           self.send_button.setText(_("Send"))
        else:
           self.password_button.hide()
           self.seed_button.hide()
           self.send_button.setText(_("Create unsigned transaction"))


    def change_password_dialog(self):
        from password_dialog import PasswordDialog
        d = PasswordDialog(self.wallet, self)
        d.run()
        self.update_lock_icon()


    def new_contact_dialog(self):
        text, ok = QInputDialog.getText(self, _('New Contact'), _('Address') + ':')
        address = unicode(text)
        if ok:
            if is_valid(address):
                self.wallet.add_contact(address)
                self.update_contacts_tab()
                self.update_history_tab()
                self.update_completions()
            else:
                QMessageBox.warning(self, _('Error'), _('Invalid Address'), _('OK'))


    def new_account_dialog(self):

        dialog = QDialog(self)
        dialog.setModal(1)
        dialog.setWindowTitle(_("New Account"))

        addr = self.wallet.new_account_address()
        vbox = QVBoxLayout()
        vbox.addWidget(QLabel(_("To create a new account, please send coins to the first address of that account:")))
        e = QLineEdit(addr)
        e.setReadOnly(True)
        vbox.addWidget(e)

        ok_button = QPushButton(_("OK"))
        ok_button.setDefault(True)
        ok_button.clicked.connect(dialog.accept)

        hbox = QHBoxLayout()
        hbox.addStretch(1)
        hbox.addWidget(ok_button)
        vbox.addLayout(hbox)

        dialog.setLayout(vbox)
        dialog.exec_()

            

    def show_master_public_key(self):
        dialog = QDialog(self)
        dialog.setModal(1)
        dialog.setWindowTitle(_("Master Public Key"))

        main_text = QTextEdit()
        main_text.setText(self.wallet.get_master_public_key())
        main_text.setReadOnly(True)
        main_text.setMaximumHeight(170)
        qrw = QRCodeWidget(self.wallet.get_master_public_key())

        ok_button = QPushButton(_("OK"))
        ok_button.setDefault(True)
        ok_button.clicked.connect(dialog.accept)

        main_layout = QGridLayout()
        main_layout.addWidget(QLabel(_('Your Master Public Key is:')), 0, 0, 1, 2)

        main_layout.addWidget(main_text, 1, 0)
        main_layout.addWidget(qrw, 1, 1 )

        vbox = QVBoxLayout()
        vbox.addLayout(main_layout)
        hbox = QHBoxLayout()
        hbox.addStretch(1)
        hbox.addWidget(ok_button)
        vbox.addLayout(hbox)

        dialog.setLayout(vbox)
        dialog.exec_()
        

    @protected
    def show_seed_dialog(self, password):
        if not self.wallet.seed:
            QMessageBox.information(parent, _('Message'), _('No seed'), _('OK'))
            return
        try:
            seed = self.wallet.decode_seed(password)
        except:
            QMessageBox.warning(self, _('Error'), _('Incorrect Password'), _('OK'))
            return

        from seed_dialog import SeedDialog
        d = SeedDialog(self)
        d.show_seed(seed, self.wallet.imported_keys)



    def show_qrcode(self, data, title = "QR code"):
        if not data: return
        d = QDialog(self)
        d.setModal(1)
        d.setWindowTitle(title)
        d.setMinimumSize(270, 300)
        vbox = QVBoxLayout()
        qrw = QRCodeWidget(data)
        vbox.addWidget(qrw, 1)
        vbox.addWidget(QLabel(data), 0, Qt.AlignHCenter)
        hbox = QHBoxLayout()
        hbox.addStretch(1)

        def print_qr(self):
            filename = "qrcode.bmp"
            bmp.save_qrcode(qrw.qr, filename)
            QMessageBox.information(None, _('Message'), _("QR code saved to file") + " " + filename, _('OK'))

        b = QPushButton(_("Save"))
        hbox.addWidget(b)
        b.clicked.connect(print_qr)

        b = QPushButton(_("Close"))
        hbox.addWidget(b)
        b.clicked.connect(d.accept)
        b.setDefault(True)

        vbox.addLayout(hbox)
        d.setLayout(vbox)
        d.exec_()


    def do_protect(self, func, args):
        if self.wallet.use_encryption:
            password = self.password_dialog()
            if not password:
                return
        else:
            password = None
            
        if args != (False,):
            args = (self,) + args + (password,)
        else:
            args = (self,password)
        apply( func, args)


    @protected
    def show_private_key(self, address, password):
        if not address: return
        try:
            pk_list = self.wallet.get_private_key(address, password)
        except BaseException, e:
            self.show_message(str(e))
            return
        QMessageBox.information(self, _('Private key'), 'Address'+ ': ' + address + '\n\n' + _('Private key') + ': ' + '\n'.join(pk_list), _('OK'))


    @protected
    def do_sign(self, address, message, signature, password):
        try:
            sig = self.wallet.sign_message(str(address.text()), str(message.toPlainText()), password)
            signature.setText(sig)
        except BaseException, e:
            self.show_message(str(e))

    def sign_message(self, address):
        if not address: return
        d = QDialog(self)
        d.setModal(1)
        d.setWindowTitle(_('Sign Message'))
        d.setMinimumSize(410, 290)

        tab_widget = QTabWidget()
        tab = QWidget()
        layout = QGridLayout(tab)

        sign_address = QLineEdit()

        sign_address.setText(address)
        layout.addWidget(QLabel(_('Address')), 1, 0)
        layout.addWidget(sign_address, 1, 1)

        sign_message = QTextEdit()
        layout.addWidget(QLabel(_('Message')), 2, 0)
        layout.addWidget(sign_message, 2, 1)
        layout.setRowStretch(2,3)

        sign_signature = QTextEdit()
        layout.addWidget(QLabel(_('Signature')), 3, 0)
        layout.addWidget(sign_signature, 3, 1)
        layout.setRowStretch(3,1)


        hbox = QHBoxLayout()
        b = QPushButton(_("Sign"))
        hbox.addWidget(b)
        b.clicked.connect(lambda: self.do_sign(sign_address, sign_message, sign_signature))
        b = QPushButton(_("Close"))
        b.clicked.connect(d.accept)
        hbox.addWidget(b)
        layout.addLayout(hbox, 4, 1)
        tab_widget.addTab(tab, _("Sign"))


        tab = QWidget()
        layout = QGridLayout(tab)

        verify_address = QLineEdit()
        layout.addWidget(QLabel(_('Address')), 1, 0)
        layout.addWidget(verify_address, 1, 1)

        verify_message = QTextEdit()
        layout.addWidget(QLabel(_('Message')), 2, 0)
        layout.addWidget(verify_message, 2, 1)
        layout.setRowStretch(2,3)

        verify_signature = QTextEdit()
        layout.addWidget(QLabel(_('Signature')), 3, 0)
        layout.addWidget(verify_signature, 3, 1)
        layout.setRowStretch(3,1)

        def do_verify():
            if self.wallet.verify_message(verify_address.text(), str(verify_signature.toPlainText()), str(verify_message.toPlainText())):
                self.show_message(_("Signature verified"))
            else:
                self.show_message(_("Error: wrong signature"))

        hbox = QHBoxLayout()
        b = QPushButton(_("Verify"))
        b.clicked.connect(do_verify)
        hbox.addWidget(b)
        b = QPushButton(_("Close"))
        b.clicked.connect(d.accept)
        hbox.addWidget(b)
        layout.addLayout(hbox, 4, 1)
        tab_widget.addTab(tab, _("Verify"))

        vbox = QVBoxLayout()
        vbox.addWidget(tab_widget)
        d.setLayout(vbox)
        d.exec_()

        


    def question(self, msg):
        return QMessageBox.question(self, _('Message'), msg, QMessageBox.Yes | QMessageBox.No, QMessageBox.No) == QMessageBox.Yes

    def show_message(self, msg):
        QMessageBox.information(self, _('Message'), msg, _('OK'))

    def password_dialog(self ):
        d = QDialog(self)
        d.setModal(1)

        pw = QLineEdit()
        pw.setEchoMode(2)

        vbox = QVBoxLayout()
        msg = _('Please enter your password')
        vbox.addWidget(QLabel(msg))

        grid = QGridLayout()
        grid.setSpacing(8)
        grid.addWidget(QLabel(_('Password')), 1, 0)
        grid.addWidget(pw, 1, 1)
        vbox.addLayout(grid)

        vbox.addLayout(ok_cancel_buttons(d))
        d.setLayout(vbox)

        self.run_hook('password_dialog', pw, grid, 1)
        if not d.exec_(): return
        return unicode(pw.text())








    def tx_from_text(self, txt):
        "json or raw hexadecimal"
        try:
            txt.decode('hex')
            tx = Transaction(txt)
            return tx
        except:
            pass

        try:
            tx_dict = json.loads(str(txt))
            assert "hex" in tx_dict.keys()
            assert "complete" in tx_dict.keys()
            if not tx_dict["complete"]:
                assert "input_info" in tx_dict.keys()
            tx = Transaction(tx_dict["hex"])
            return tx
        except:
            pass
        
        QMessageBox.critical(None, "Unable to parse transaction", _("Electrum was unable to parse your transaction"))



    def read_tx_from_file(self):
        fileName = self.getOpenFileName(_("Select your transaction file"), "*.txn")
        if not fileName:
            return
        try:
            with open(fileName, "r") as f:
                file_content = f.read()
        except (ValueError, IOError, os.error), reason:
            QMessageBox.critical(None,"Unable to read file or no transaction found", _("Electrum was unable to open your transaction file") + "\n" + str(reason))

        return self.tx_from_text(file_content)


    @protected
    def sign_raw_transaction(self, tx, input_info, password):
        self.wallet.signrawtransaction(tx, input_info, [], password)

    def do_process_from_text(self):
        text = text_dialog(self, _('Input raw transaction'), _("Transaction:"), _("Load transaction"))
        if not text:
            return
        tx = self.tx_from_text(text)
        if tx:
            self.show_transaction(tx)

    def do_process_from_file(self):
        tx = self.read_tx_from_file()
        if tx:
            self.show_transaction(tx)

    def do_process_from_csvReader(self, csvReader):
        outputs = []
        try:
            for row in csvReader:
                address = row[0]
                amount = float(row[1])
                amount = int(100000000*amount)
                outputs.append((address, amount))
        except (ValueError, IOError, os.error), reason:
            QMessageBox.critical(None,"Unable to read file or no transaction found", _("Electrum was unable to open your transaction file") + "\n" + str(reason))
            return

        try:
            tx = self.wallet.make_unsigned_transaction(outputs, None, None, account=self.current_account)
        except BaseException, e:
            self.show_message(str(e))
            return

        self.show_transaction(tx)

    def do_process_from_csv_file(self):
        fileName = self.getOpenFileName(_("Select your transaction CSV"), "*.csv")
        if not fileName:
            return
        try:
            with open(fileName, "r") as f:
                csvReader = csv.reader(f)
                self.do_process_from_csvReader(csvReader)
        except (ValueError, IOError, os.error), reason:
            QMessageBox.critical(None,"Unable to read file or no transaction found", _("Electrum was unable to open your transaction file") + "\n" + str(reason))
            return

    def do_process_from_csv_text(self):
        text = text_dialog(self, _('Input CSV'), _("CSV:"), _("Load CSV"))
        if not text:
            return
        f = StringIO.StringIO(text)
        csvReader = csv.reader(f)
        self.do_process_from_csvReader(csvReader)



    @protected
    def do_export_privkeys(self, password):
        self.show_message("%s\n%s\n%s" % (_("WARNING: ALL your private keys are secret."),  _("Exposing a single private key can compromise your entire wallet!"), _("In particular, DO NOT use 'redeem private key' services proposed by third parties.")))

        try:
            select_export = _('Select file to export your private keys to')
            fileName = self.getSaveFileName(select_export, 'electrum-private-keys.csv', "*.csv")
            if fileName:
                with open(fileName, "w+") as csvfile:
                    transaction = csv.writer(csvfile)
                    transaction.writerow(["address", "private_key"])

                    addresses = self.wallet.addresses(True)
                    
                    for addr in addresses:
                        pk = "".join(self.wallet.get_private_key(addr, password))
                        transaction.writerow(["%34s"%addr,pk])

                    self.show_message(_("Private keys exported."))

        except (IOError, os.error), reason:
            export_error_label = _("Electrum was unable to produce a private key-export.")
            QMessageBox.critical(None,"Unable to create csv", export_error_label + "\n" + str(reason))

        except BaseException, e:
          self.show_message(str(e))
          return


    def do_import_labels(self):
        labelsFile = self.getOpenFileName(_("Open labels file"), "*.dat")
        if not labelsFile: return
        try:
            f = open(labelsFile, 'r')
            data = f.read()
            f.close()
            for key, value in json.loads(data).items():
                self.wallet.set_label(key, value)
            QMessageBox.information(None, _("Labels imported"), _("Your labels were imported from")+" '%s'" % str(labelsFile))
        except (IOError, os.error), reason:
            QMessageBox.critical(None, _("Unable to import labels"), _("Electrum was unable to import your labels.")+"\n" + str(reason))
            

    def do_export_labels(self):
        labels = self.wallet.labels
        try:
            fileName = self.getSaveFileName(_("Select file to save your labels"), 'electrum_labels.dat', "*.dat")
            if fileName:
                with open(fileName, 'w+') as f:
                    json.dump(labels, f)
                QMessageBox.information(None, "Labels exported", _("Your labels where exported to")+" '%s'" % str(fileName))
        except (IOError, os.error), reason:
            QMessageBox.critical(None, "Unable to export labels", _("Electrum was unable to export your labels.")+"\n" + str(reason))


    def do_export_history(self):
        from lite_window import csv_transaction
        csv_transaction(self.wallet)


    @protected
    def do_import_privkey(self, password):
        if not self.wallet.imported_keys:
            r = QMessageBox.question(None, _('Warning'), '<b>'+_('Warning') +':\n</b><br/>'+ _('Imported keys are not recoverable from seed.') + ' ' \
                                         + _('If you ever need to restore your wallet from its seed, these keys will be lost.') + '<p>' \
                                         + _('Are you sure you understand what you are doing?'), 3, 4)
            if r == 4: return

        text = text_dialog(self, _('Import private keys'), _("Enter private keys")+':', _("Import"))
        if not text: return

        text = str(text).split()
        badkeys = []
        addrlist = []
        for key in text:
            try:
                addr = self.wallet.import_key(key, password)
            except BaseException as e:
                badkeys.append(key)
                continue
            if not addr: 
                badkeys.append(key)
            else:
                addrlist.append(addr)
        if addrlist:
            QMessageBox.information(self, _('Information'), _("The following addresses were added") + ':\n' + '\n'.join(addrlist))
        if badkeys:
            QMessageBox.critical(self, _('Error'), _("The following inputs could not be imported") + ':\n'+ '\n'.join(badkeys))
        self.update_receive_tab()
        self.update_history_tab()


    def settings_dialog(self):
        d = QDialog(self)
        d.setWindowTitle(_('Electrum Settings'))
        d.setModal(1)
        vbox = QVBoxLayout()

        tabs = QTabWidget(self)
        self.settings_tab = tabs
        vbox.addWidget(tabs)

        tab1 = QWidget()
        grid_ui = QGridLayout(tab1)
        grid_ui.setColumnStretch(0,1)
        tabs.addTab(tab1, _('Display') )

        nz_label = QLabel(_('Display zeros'))
        grid_ui.addWidget(nz_label, 0, 0)
        nz_e = AmountEdit(None,True)
        nz_e.setText("%d"% self.num_zeros)
        grid_ui.addWidget(nz_e, 0, 1)
        msg = _('Number of zeros displayed after the decimal point. For example, if this is set to 2, "1." will be displayed as "1.00"')
        grid_ui.addWidget(HelpButton(msg), 0, 2)
        if not self.config.is_modifiable('num_zeros'):
            for w in [nz_e, nz_label]: w.setEnabled(False)
        
        lang_label=QLabel(_('Language') + ':')
        grid_ui.addWidget(lang_label, 1, 0)
        lang_combo = QComboBox()
        from electrum.i18n import languages
        lang_combo.addItems(languages.values())
        try:
            index = languages.keys().index(self.config.get("language",''))
        except:
            index = 0
        lang_combo.setCurrentIndex(index)
        grid_ui.addWidget(lang_combo, 1, 1)
        grid_ui.addWidget(HelpButton(_('Select which language is used in the GUI (after restart).')+' '), 1, 2)
        if not self.config.is_modifiable('language'):
            for w in [lang_combo, lang_label]: w.setEnabled(False)

        currencies = self.exchanger.get_currencies()
        currencies.insert(0, "None")

        cur_label=QLabel(_('Currency') + ':')
        grid_ui.addWidget(cur_label , 2, 0)
        cur_combo = QComboBox()
        cur_combo.addItems(currencies)
        try:
            index = currencies.index(self.config.get('currency', "None"))
        except:
            index = 0
        cur_combo.setCurrentIndex(index)
        grid_ui.addWidget(cur_combo, 2, 1)
        grid_ui.addWidget(HelpButton(_('Select which currency is used for quotes.')+' '), 2, 2)
        
        expert_cb = QCheckBox(_('Expert mode'))
        expert_cb.setChecked(self.expert_mode)
        grid_ui.addWidget(expert_cb, 3, 0)
        hh =  _('In expert mode, your client will:') + '\n'  \
            + _(' - Show change addresses in the Receive tab') + '\n'  \
            + _(' - Display the balance of each address') + '\n'  \
            + _(' - Add freeze/prioritize actions to addresses.') 
        grid_ui.addWidget(HelpButton(hh), 3, 2)
        grid_ui.setRowStretch(4,1)

        # wallet tab
        tab2 = QWidget()
        grid_wallet = QGridLayout(tab2)
        grid_wallet.setColumnStretch(0,1)
        tabs.addTab(tab2, _('Wallet') )
        
        fee_label = QLabel(_('Transaction fee'))
        grid_wallet.addWidget(fee_label, 0, 0)
        fee_e = AmountEdit(self.base_unit)
        fee_e.setText(self.format_amount(self.wallet.fee).strip())
        grid_wallet.addWidget(fee_e, 0, 2)
        msg = _('Fee per kilobyte of transaction.') + ' ' \
            + _('Recommended value') + ': ' + self.format_amount(50000)
        grid_wallet.addWidget(HelpButton(msg), 0, 3)
        if not self.config.is_modifiable('fee_per_kb'):
            for w in [fee_e, fee_label]: w.setEnabled(False)

        usechange_cb = QCheckBox(_('Use change addresses'))
        usechange_cb.setChecked(self.wallet.use_change)
        grid_wallet.addWidget(usechange_cb, 1, 0)
        grid_wallet.addWidget(HelpButton(_('Using change addresses makes it more difficult for other people to track your transactions.')+' '), 1, 3)
        if not self.config.is_modifiable('use_change'): usechange_cb.setEnabled(False)

        units = ['BTC', 'mBTC']
        unit_label = QLabel(_('Base unit'))
        grid_wallet.addWidget(unit_label, 3, 0)
        unit_combo = QComboBox()
        unit_combo.addItems(units)
        unit_combo.setCurrentIndex(units.index(self.base_unit()))
        grid_wallet.addWidget(unit_combo, 3, 2)
        grid_wallet.addWidget(HelpButton(_('Base unit of your wallet.')\
                                             + '\n1BTC=1000mBTC.\n' \
                                             + _(' This settings affects the fields in the Send tab')+' '), 3, 3)
        grid_wallet.setRowStretch(4,1)

        # plugins
        if self.plugins:
            tab5 = QScrollArea()
            tab5.setEnabled(True)
            tab5.setWidgetResizable(True)

            grid_plugins = QGridLayout()
            grid_plugins.setColumnStretch(0,1)

            w = QWidget()
            w.setLayout(grid_plugins)
            tab5.setWidget(w)

            w.setMinimumHeight(len(self.plugins)*35)

            tabs.addTab(tab5, _('Plugins') )
            def mk_toggle(cb, p):
                return lambda: cb.setChecked(p.toggle())
            for i, p in enumerate(self.plugins):
                try:
                    cb = QCheckBox(p.fullname())
                    cb.setDisabled(not p.is_available())
                    cb.setChecked(p.is_enabled())
                    cb.clicked.connect(mk_toggle(cb,p))
                    grid_plugins.addWidget(cb, i, 0)
                    if p.requires_settings():
                        grid_plugins.addWidget(EnterButton(_('Settings'), p.settings_dialog), i, 1)
                    grid_plugins.addWidget(HelpButton(p.description()), i, 2)
                except:
                    print_msg("Error: cannot display plugin", p)
                    traceback.print_exc(file=sys.stdout)
            grid_plugins.setRowStretch(i+1,1)

        self.run_hook('create_settings_tab', tabs)

        vbox.addLayout(ok_cancel_buttons(d))
        d.setLayout(vbox) 

        # run the dialog
        if not d.exec_(): return

        fee = unicode(fee_e.text())
        try:
            fee = self.read_amount(fee)
        except:
            QMessageBox.warning(self, _('Error'), _('Invalid value') +': %s'%fee, _('OK'))
            return

        self.wallet.set_fee(fee)
        
        nz = unicode(nz_e.text())
        try:
            nz = int( nz )
            if nz>8: nz=8
        except:
            QMessageBox.warning(self, _('Error'), _('Invalid value')+':%s'%nz, _('OK'))
            return

        if self.num_zeros != nz:
            self.num_zeros = nz
            self.config.set_key('num_zeros', nz, True)
            self.update_history_tab()
            self.update_receive_tab()

        usechange_result = usechange_cb.isChecked()
        if self.wallet.use_change != usechange_result:
            self.wallet.use_change = usechange_result
            self.config.set_key('use_change', self.wallet.use_change, True)
        
        unit_result = units[unit_combo.currentIndex()]
        if self.base_unit() != unit_result:
            self.decimal_point = 8 if unit_result == 'BTC' else 5
            self.config.set_key('decimal_point', self.decimal_point, True)
            self.update_history_tab()
            self.update_status()
        
        need_restart = False

        lang_request = languages.keys()[lang_combo.currentIndex()]
        if lang_request != self.config.get('language'):
            self.config.set_key("language", lang_request, True)
            need_restart = True
            
        cur_request = str(currencies[cur_combo.currentIndex()])
        if cur_request != self.config.get('currency', "None"):
            self.config.set_key('currency', cur_request, True)
            self.update_wallet()

        self.run_hook('close_settings_dialog')

        if need_restart:
            QMessageBox.warning(self, _('Success'), _('Please restart Electrum to activate the new GUI settings'), _('OK'))

        self.receive_tab_set_mode(expert_cb.isChecked())

    def run_network_dialog(self):
        NetworkDialog(self.wallet.network, self.config, self).do_exec()

    def closeEvent(self, event):
        g = self.geometry()
        self.config.set_key("winpos-qt", [g.left(),g.top(),g.width(),g.height()], True)
        self.save_column_widths()
        self.config.set_key("console-history", self.console.history[-50:], True)
        event.accept()

