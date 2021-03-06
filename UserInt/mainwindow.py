import pytz
import alarms_cl_db
import datetime

from PyQt5.QtSql import QSqlQuery
from PyQt5.QtWidgets import QMainWindow, QButtonGroup, \
                            QSystemTrayIcon, QFileDialog
from PyQt5.QtGui import QCloseEvent, QIcon
from PyQt5.QtCore import QTimer, QTime, QUrl, QSettings, QEvent
from PyQt5.QtMultimedia import QMediaPlaylist, QMediaPlayer, QMediaContent

from UserInt.mainwindow_ui import Ui_MainWindow
from config import SETTINGS_FILE_NAME, DIR_ICONS, DIR_MUSIC


def get_total_seconds(t: QTime) -> int:
    return t.hour() * 3600 + t.minute() * 60 + t.second()


def add_to_current_time(t: QTime) -> QTime:
    secs = get_total_seconds(t)
    return QTime.currentTime().addSecs(secs)


def count_record():
    query = QSqlQuery("alarm_clock_db.sqlite")
    query.exec("""SELECT COUNT(*) FROM list WHERE id > 0""")
    query.next()
    count = query.record().value(0)
    return count


class MainWindow(QMainWindow):
    def __init__(self):
        super().__init__()

        self.ui = Ui_MainWindow()
        self.ui.setupUi(self)

        # иконка будильника и установка на окно
        self.icon = QIcon(str(DIR_ICONS / 'alarm-clock.png'))
        self.setWindowIcon(self.icon)

        # трекер на панели задач
        self.tray = QSystemTrayIcon(self.icon)
        self.tray.setToolTip(self.windowTitle())
        self.tray.activated.connect(self._on_tray_activated)
        self.tray.show()

        # настройки окна
        self.read_settings()

        # группировка радиобаттон
        self._button_group = QButtonGroup()
        self._button_group.addButton(self.ui.at_time_rb)
        self._button_group.addButton(self.ui.through_time_rb)
        self._button_group.buttonClicked.connect(self._update_states)

        self.ui.start.clicked.connect(self._start)
        self.ui.stop.clicked.connect(self._stop)
        self.ui.more_sleep.clicked.connect(self._more_sleep)
        self.ui.i_woke_up.clicked.connect(self._i_woke_up)
        self.ui.audio_selection.clicked.connect(self._audio_selection)
        self.ui.db_btn.clicked.connect(self._show_list)

        # таймер для циферблата
        clock = QTimer(self)
        clock.timeout.connect(self.show_time)
        clock.start(100)

        # таймер для будильника
        self._timer = QTimer()
        self._timer.setInterval(100)
        self._timer.timeout.connect(self._tick)

        # таймер для повышения громкости мелодии
        self._timer_inc_volume = QTimer()
        self._timer_inc_volume.setInterval(1000)
        self._timer_inc_volume.timeout.connect(self._inc_volume_tick)

        self._woke_up = False
        self._alarm_time: QTime

        self.playlist = QMediaPlaylist()
        self.playlist.setPlaybackMode(QMediaPlaylist.Loop)

        url = QUrl.fromLocalFile(str(DIR_MUSIC / 'Signals.mp3'))
        self.playlist.addMedia(QMediaContent(url))
        self.player = QMediaPlayer()
        self.player.setPlaylist(self.playlist)

        alarms_cl_db.List(self)
        self._call()
        self._update_states()

        self.ui.timezone_cb.addItem("Local time")
        self.ui.timezone_cb.addItems(pytz.common_timezones)
        self.ui.timezone_cb.activated[str].connect(self.show_time)

    # время на циферблате
    def show_time(self):
        if self.ui.timezone_cb.currentText() == "Local time":
            time = QTime.currentTime()
            label = time.toString('hh:mm:ss')
            self.ui.current_time.setText(label)
        else:
            timezone = pytz.timezone(f'{self.ui.timezone_cb.currentText()}')
            time = datetime.datetime.now(timezone)
            self.ui.current_time.setText(time.strftime("%H:%M:%S"))
            self._call()

    def _update_states(self):
        self.ui.at_time.setEnabled(self.ui.at_time_rb.isChecked())
        self.ui.through_time.setEnabled(self.ui.through_time_rb.isChecked())
        self.ui.i_woke_up.setVisible(self._woke_up)
        self.ui.more_sleep.setVisible(self._woke_up)

        self.ui.start.setVisible(not self._woke_up)
        self.ui.audio_selection.setVisible(not self._woke_up)
        self.ui.at_time.setVisible(not self._woke_up)
        self.ui.at_time_rb.setVisible(not self._woke_up)

        if self.ui.time_remaining.text() == "":
            self.ui.stop.setVisible(False)
        else:
            self.ui.stop.setVisible(True)

        # Корректировка высоты окна после возможного скрытия кнопок
        self.resize(self.width(), self.minimumHeight())

    def _inc_volume_tick(self):
        if self.player.volume() >= 100:
            self._timer_inc_volume.stop()

        self.player.setVolume(self.player.volume() + 1)

    def _tick(self):
        if self.ui.timezone_cb.currentText() != "Local time":
            timezone = pytz.timezone(f'{self.ui.timezone_cb.currentText()}')
            time_str = datetime.datetime.now(timezone).strftime("%H:%M:%S")
            remain = QTime.fromString(time_str).secsTo(self._alarm_time)
        else:
            remain = QTime.currentTime().secsTo(self._alarm_time)
        if remain == 0:
            self._woke_up = True
            self._timer.stop()
            self.ui.time_remaining.setText("")
            self._update_states()
            self.player.setVolume(1)
            self.player.play()
            self._timer_inc_volume.start()
            self._set_visible(True)
        else:
            if remain < 0:
                remain += 24 * 3600

            hh, mm = divmod(remain, 3600)
            mm, ss = divmod(mm, 60)

            alarm_str = self._alarm_time.toString('hh:mm:ss')
            self.ui.time_remaining.setText(f"Ближайший будильник установлен"
                                           f" на {alarm_str}. Прозвенит через:"
                                           f" {hh:0>2}:{mm:0>2}:{ss:0>2}")
            self._update_states()

    def _i_woke_up(self):
        self._woke_up = False
        self.player.stop()
        self.ui.time_remaining.setText("")
        self._update_states()
        alarms_cl_db.List(self).delete_record(self._alarm_time.toString())
        self._call()

    def _start(self):
        self._woke_up = False

        if self.ui.at_time_rb.isChecked():
            alarms_cl_db.List(self).create_new_record(
                self.ui.at_time.time().toString())

        elif self.ui.through_time_rb.isChecked():
            t = self.ui.through_time.time()
            self.call = add_to_current_time(t)
            alarms_cl_db.List(self).create_new_record(self.call.toString())

        self._call()

    # установка ближайшего будильникаdd
    def _call(self):
        count = count_record()

        if count != 0:
            query = QSqlQuery("alarm_clock_db.sqlite")
            query.exec("""SELECT * FROM list ORDER BY al_time""")
            query.next()
            self._alarm_time = QTime.fromString(query.value('al_time'))
            while query.next():
                if self.ui.current_time.text() < query.value('al_time'):
                    if self.ui.current_time.text() < \
                            self._alarm_time.toString() and \
                            query.value('al_time') < \
                            self._alarm_time.toString():
                        self._alarm_time = QTime.fromString(
                            query.value('al_time'))
                        break
                    elif self.ui.current_time.text() > \
                            self._alarm_time.toString():
                        self._alarm_time = QTime.fromString(
                            query.value('al_time'))

            self._timer.start()
            self._update_states()
        else:
            self.ui.time_remaining.setText("")
            self._update_states()

    def _stop(self):
        self._timer.stop()
        self.ui.time_remaining.setText("")
        self._update_states()
        alarms_cl_db.List(self).delete_record(self._alarm_time.toString())
        self._call()

    def _more_sleep(self):
        self._i_woke_up()

        t = self.ui.through_time.time()
        self.call = add_to_current_time(t)
        alarms_cl_db.List(self).create_new_record(self.call.toString())
        self._call()

    def _show_list(self):
        if not alarms_cl_db.create_connection():
            exit(1)
        win = alarms_cl_db.List(self)
        win.show()

    def _audio_selection(self):
        audio_filename, _ = QFileDialog.getOpenFileName(self)

        if audio_filename != '':
            url = QUrl.fromLocalFile(audio_filename)
        else:
            url = QUrl.fromLocalFile(str(DIR_MUSIC / 'Signals.mp3'))

        self.playlist.clear()
        self.playlist.addMedia(QMediaContent(url))
        self.player = QMediaPlayer()
        self.player.setPlaylist(self.playlist)

    def _set_visible(self, visible: bool):
        self.setVisible(visible)

        if visible:
            self.showNormal()
            self.activateWindow()

    def _on_tray_activated(self):
        self._set_visible(not self.isVisible())

    def changeEvent(self, event: QEvent):
        if event.type() == QEvent.WindowStateChange:
            # Если окно свернули
            if self.isMinimized():
                # Прячем окно с панели задач
                QTimer.singleShot(0, self.hide)

    def read_settings(self):
        ini = QSettings(SETTINGS_FILE_NAME, QSettings.IniFormat)

        if state := ini.value('MainWindow_State'):
            self.restoreState(state)

        if geometry := ini.value('MainWindow_Geometry'):
            self.restoreGeometry(geometry)

        if at_time_rb := ini.value('at_time_rb', type=bool):
            self.ui.at_time_rb.setChecked(at_time_rb)

        if through_time_rb := ini.value('through_time_rb', type=bool):
            self.ui.through_time_rb.setChecked(through_time_rb)

        if at_time := ini.value('at_time'):
            self.ui.at_time.setTime(at_time)

        if through_time := ini.value('through_time'):
            self.ui.through_time.setTime(through_time)

    def write_settings(self):
        ini = QSettings(SETTINGS_FILE_NAME, QSettings.IniFormat)
        ini.setValue('MainWindow_State', self.saveState())
        ini.setValue('MainWindow_Geometry', self.saveGeometry())

        ini.setValue('at_time_rb', self.ui.at_time_rb.isChecked())
        ini.setValue('through_time_rb', self.ui.through_time_rb.isChecked())

    def closeEvent(self, event: QCloseEvent):
        self.write_settings()
