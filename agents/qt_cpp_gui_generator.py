"""
Qt C++ GUI Generator for Silicon to Software (S2S).

Qt 5.14.2 | QMake (.pro) | MinGW 32/64-bit | Windows 10
All UI defined in Qt Designer .ui files — no dynamic widget creation in C++.
Promoted widgets in MainWindow.ui for each sub-panel.
Separate ClassName.h / .cpp / .ui per class.
"""

import logging
from typing import Optional, Dict, List

logger = logging.getLogger(__name__)


from agents import qt_baseline


class QtCppGuiGenerator:
    """Generate a Qt 5.14.2 C++ GUI application skeleton."""

    # Application-class -> primary panel preset table.
    # Each entry: (class_name, title, kind) where kind picks a chart shape
    # in qt_baseline.application_panel_cpp.
    _APP_PRESETS = {
        "radar":       [("SpectrumPanel",  "Live spectrum",     "spectrum"),
                        ("RangeDopplerPanel", "Range / Doppler", "ranging"),
                        ("RadarThreatPanel",  "Tracked targets", "threats")],
        "ew":          [("ThreatPanel",    "Threat table",      "threats"),
                        ("DfPanel",        "Direction of arrival", "spectrum"),
                        ("InterceptPanel", "Intercept history", "ranging")],
        "ew_signint":  [("ThreatPanel",    "Threat table",      "threats"),
                        ("DfPanel",        "Direction of arrival", "spectrum"),
                        ("InterceptPanel", "Intercept history", "ranging")],
        "satcom":      [("LinkBudgetPanel", "Link budget",      "link_budget"),
                        ("AcquisitionPanel","Acquisition state","ranging"),
                        ("TrackingPanel",   "Tracking",         "spectrum")],
        "communication":[("ConstellationPanel", "Constellation","spectrum"),
                         ("LinkPanel",          "Link metrics", "link_budget")],
        "comms":        [("ConstellationPanel", "Constellation","spectrum"),
                         ("LinkPanel",          "Link metrics", "link_budget")],
        "general":     [("OverviewPanel",  "Overview",          "generic")],
    }

    def generate(
        self,
        project_name: str,
        design_type: str = "Digital",
        driver_info: Optional[Dict] = None,
        peripherals: Optional[List[Dict]] = None,
        application_class: str = "general",
    ) -> Dict[str, str]:
        safe = self._safe_class_name(project_name)
        peripherals = peripherals or []

        files = {
            f"qt_gui/{safe}.pro":          self._pro(
                safe, project_name, peripherals, application_class,
            ),
            "qt_gui/main.cpp":             self._main_cpp(safe, project_name),
            "qt_gui/MainWindow.h":         self._mainwindow_h(project_name),
            "qt_gui/MainWindow.cpp":       self._mainwindow_cpp(
                project_name, design_type, peripherals, application_class,
            ),
            "qt_gui/MainWindow.ui":        self._mainwindow_ui(safe, project_name),
            "qt_gui/SerialWorker.h":       self._serialworker_h(),
            "qt_gui/SerialWorker.cpp":     self._serialworker_cpp(),
            "qt_gui/DashboardPanel.h":     self._dashboard_h(),
            "qt_gui/DashboardPanel.cpp":   self._dashboard_cpp(project_name),
            "qt_gui/DashboardPanel.ui":    self._dashboard_ui(project_name),
            "qt_gui/ControlPanel.h":       self._control_h(),
            "qt_gui/ControlPanel.cpp":     self._control_cpp(),
            "qt_gui/ControlPanel.ui":      self._control_ui(),
            "qt_gui/LogPanel.h":           self._log_h(),
            "qt_gui/LogPanel.cpp":         self._log_cpp(),
            "qt_gui/LogPanel.ui":          self._log_ui(),
            "qt_gui/SettingsPanel.h":      self._settings_h(),
            "qt_gui/SettingsPanel.cpp":    self._settings_cpp(),
            "qt_gui/SettingsPanel.ui":     self._settings_ui(),
            # Mandatory baseline (2026-05-01) - every generated GUI ships
            # these regardless of project. See agents/qt_baseline.py.
            "qt_gui/EventLogger.h":   qt_baseline.eventlogger_h(),
            "qt_gui/EventLogger.cpp": qt_baseline.eventlogger_cpp(),
            "qt_gui/EventLogPanel.h": qt_baseline.eventlogpanel_h(),
            "qt_gui/EventLogPanel.cpp": qt_baseline.eventlogpanel_cpp(),
            "qt_gui/UserManager.h":   qt_baseline.usermanager_h(),
            "qt_gui/UserManager.cpp": qt_baseline.usermanager_cpp(),
            "qt_gui/LoginDialog.h":   qt_baseline.logindialog_h(),
            "qt_gui/LoginDialog.cpp": qt_baseline.logindialog_cpp(project_name),
            "qt_gui/UsersPanel.h":    qt_baseline.userspanel_h(),
            "qt_gui/UsersPanel.cpp":  qt_baseline.userspanel_cpp(),
            "qt_gui/AboutDialog.h":   qt_baseline.aboutdialog_h(),
            "qt_gui/AboutDialog.cpp": qt_baseline.aboutdialog_cpp(project_name),
            "qt_gui/ForcePasswordChangeDialog.h":   qt_baseline.force_password_change_h(),
            "qt_gui/ForcePasswordChangeDialog.cpp": qt_baseline.force_password_change_cpp(),
            # .ui XML for every dialog/panel so Qt Designer can open them.
            "qt_gui/LoginDialog.ui":     qt_baseline.logindialog_ui(project_name),
            "qt_gui/AboutDialog.ui":     qt_baseline.aboutdialog_ui(project_name),
            "qt_gui/UsersPanel.ui":      qt_baseline.userspanel_ui(),
            "qt_gui/EventLogPanel.ui":   qt_baseline.eventlogpanel_ui(),
            # Welcome / overview tab (Item #6) - first tab on launch.
            "qt_gui/WelcomePanel.h":   qt_baseline.welcome_panel_h(),
            "qt_gui/WelcomePanel.cpp": qt_baseline.welcome_panel_cpp(
                project_name, application_class,
                len(peripherals),
                len(getattr(self, "_brief_register_count_list", []) or []),
            ),
            "qt_gui/WelcomePanel.ui":  qt_baseline.welcome_panel_ui(project_name),
        }

        # Per-peripheral diagnostic panel - one per peripheral the
        # ProjectBrief discovered. Two distinct projects produce two
        # distinct sets of panel classes.
        for p in peripherals:
            slug = self._safe_class_name(p.get("name", "peripheral"))
            cls = f"Peripheral{slug}Panel"
            files[f"qt_gui/{cls}.h"] = qt_baseline.peripheral_panel_h(cls)
            files[f"qt_gui/{cls}.cpp"] = qt_baseline.peripheral_panel_cpp(
                cls, p.get("name", "peripheral"),
                str(p.get("bus", "generic")),
                str(p.get("address", "")),
            )
            files[f"qt_gui/{cls}.ui"] = qt_baseline.peripheral_panel_ui(
                cls, p.get("name", "peripheral"), str(p.get("bus", "generic")),
            )

        # Application-class primary panels.
        preset = self._APP_PRESETS.get(
            application_class.lower(),
            self._APP_PRESETS["general"],
        )
        for cls, title, kind in preset:
            files[f"qt_gui/{cls}.h"] = qt_baseline.application_panel_h(cls)
            files[f"qt_gui/{cls}.cpp"] = qt_baseline.application_panel_cpp(
                cls, title, kind,
            )
            files[f"qt_gui/{cls}.ui"] = qt_baseline.application_panel_ui(cls, title)
        return files

    # ------------------------------------------------------------------ #
    # QMake .pro
    # ------------------------------------------------------------------ #

    def _pro(self, safe: str, project_name: str,
             peripherals: Optional[List[Dict]] = None,
             application_class: str = "general") -> str:
        peripherals = peripherals or []
        # Per-peripheral panel sources / headers.
        per_srcs = "".join(
            f"    Peripheral{self._safe_class_name(p.get('name','peripheral'))}Panel.cpp \\\n"
            for p in peripherals
        )
        per_hdrs = "".join(
            f"    Peripheral{self._safe_class_name(p.get('name','peripheral'))}Panel.h \\\n"
            for p in peripherals
        )
        # Application-class panel sources.
        preset = self._APP_PRESETS.get(application_class.lower(),
                                       self._APP_PRESETS["general"])
        app_srcs = "".join(f"    {cls}.cpp \\\n" for cls, *_ in preset)
        app_hdrs = "".join(f"    {cls}.h \\\n"   for cls, *_ in preset)
        return f"""\
# {project_name} - Qt GUI Application
# Generated by Silicon to Software (S2S) v2
# Qt 5.14.2 | QMake | MinGW 32/64-bit | Windows 10

QT       += core gui widgets serialport charts
greaterThan(QT_MAJOR_VERSION, 4): QT += widgets

TARGET   = {safe}
TEMPLATE = app
CONFIG  += c++14
DEFINES += QT_DEPRECATED_WARNINGS

SOURCES += \\
    main.cpp \\
    MainWindow.cpp \\
    SerialWorker.cpp \\
    DashboardPanel.cpp \\
    ControlPanel.cpp \\
    LogPanel.cpp \\
    SettingsPanel.cpp \\
    EventLogger.cpp \\
    EventLogPanel.cpp \\
    UserManager.cpp \\
    LoginDialog.cpp \\
    UsersPanel.cpp \\
    AboutDialog.cpp \\
    ForcePasswordChangeDialog.cpp \\
{per_srcs}{app_srcs}

HEADERS += \\
    MainWindow.h \\
    SerialWorker.h \\
    DashboardPanel.h \\
    ControlPanel.h \\
    LogPanel.h \\
    SettingsPanel.h \\
    EventLogger.h \\
    EventLogPanel.h \\
    UserManager.h \\
    LoginDialog.h \\
    UsersPanel.h \\
    AboutDialog.h \\
    ForcePasswordChangeDialog.h \\
{per_hdrs}{app_hdrs}

FORMS += \\
    MainWindow.ui \\
    DashboardPanel.ui \\
    ControlPanel.ui \\
    LogPanel.ui \\
    SettingsPanel.ui \\
    LoginDialog.ui \\
    AboutDialog.ui \\
    UsersPanel.ui \\
    EventLogPanel.ui

# Default deployment rules
qnx: target.path = /tmp/${{TARGET}}/bin
else: unix:!android: target.path = /opt/${{TARGET}}/bin
!isEmpty(target.path): INSTALLS += target
"""

    # ------------------------------------------------------------------ #
    # main.cpp
    # ------------------------------------------------------------------ #

    def _main_cpp(self, safe: str, project_name: str) -> str:
        return f"""\
#include <QApplication>
#include <QColor>
#include <QPainter>
#include <QPalette>
#include <QPixmap>
#include <QSplashScreen>
#include <QStyleFactory>
#include <QThread>
#include "MainWindow.h"
#include "LoginDialog.h"
#include "ForcePasswordChangeDialog.h"
#include "UserManager.h"
#include "EventLogger.h"

/**
 * @brief Apply Silicon to Software (S2S) v2 dark theme.
 *
 * Colour tokens:
 *   #0f1423  deep navy  (Window background)
 *   #1a2235  panel      (Base, Button)
 *   #00c6a7  teal       (Highlight, Link)
 */
static void applyDarkTheme(QApplication &app)
{{
    app.setStyle(QStyleFactory::create("Fusion"));

    QPalette p;
    p.setColor(QPalette::Window,          QColor(15,  20,  35));
    p.setColor(QPalette::WindowText,      QColor(226, 232, 240));
    p.setColor(QPalette::Base,            QColor(26,  34,  53));
    p.setColor(QPalette::AlternateBase,   QColor(42,  58,  80));
    p.setColor(QPalette::ToolTipBase,     QColor(0,   198, 167));
    p.setColor(QPalette::ToolTipText,     QColor(15,  20,  35));
    p.setColor(QPalette::Text,            QColor(226, 232, 240));
    p.setColor(QPalette::Button,          QColor(26,  34,  53));
    p.setColor(QPalette::ButtonText,      QColor(226, 232, 240));
    p.setColor(QPalette::BrightText,      QColor(0,   198, 167));
    p.setColor(QPalette::Highlight,       QColor(0,   198, 167));
    p.setColor(QPalette::HighlightedText, QColor(15,  20,  35));
    p.setColor(QPalette::Link,            QColor(0,   198, 167));
    p.setColor(QPalette::LinkVisited,     QColor(139, 92,  246));
    app.setPalette(p);

    app.setStyleSheet(
        /* Buttons */
        "QPushButton{{"
        "  background:#1a2235; color:#e2e8f0;"
        "  border:1px solid #2a3a50; border-radius:4px; padding:5px 14px;}}"
        "QPushButton:hover{{background:#2a3a50;}}"
        "QPushButton:pressed{{background:#00c6a7;color:#0f1423;}}"
        "QPushButton:disabled{{background:#0f1423;color:#475569;border-color:#1a2235;}}"
        "QPushButton#connectButton{{background:#00c6a7;color:#0f1423;font-weight:bold;}}"
        "QPushButton#connectButton:hover{{background:#00e5be;}}"
        "QPushButton#disconnectButton{{background:#dc2626;color:#fff;font-weight:bold;}}"
        "QPushButton#disconnectButton:disabled{{background:#0f1423;color:#475569;}}"
        "QPushButton#sendButton{{background:#00c6a7;color:#0f1423;font-weight:bold;}}"
        /* Tabs */
        "QTabBar::tab{{background:#1a2235;color:#94a3b8;padding:8px 20px;"
        "  border:none;margin-right:2px;}}"
        "QTabBar::tab:selected{{color:#00c6a7;border-bottom:2px solid #00c6a7;}}"
        "QTabBar::tab:hover{{color:#e2e8f0;}}"
        "QTabWidget::pane{{border:1px solid #2a3a50;}}"
        /* Table */
        "QTableWidget{{gridline-color:#2a3a50;border:1px solid #2a3a50;}}"
        "QHeaderView::section{{background:#1a2235;color:#94a3b8;"
        "  padding:4px;border:1px solid #2a3a50;}}"
        /* Inputs */
        "QComboBox{{background:#1a2235;color:#e2e8f0;"
        "  border:1px solid #2a3a50;border-radius:4px;padding:3px 8px;}}"
        "QComboBox QAbstractItemView{{background:#1a2235;color:#e2e8f0;"
        "  selection-background-color:#00c6a7;selection-color:#0f1423;}}"
        "QLineEdit{{background:#1a2235;color:#e2e8f0;"
        "  border:1px solid #2a3a50;border-radius:4px;padding:4px 8px;}}"
        "QLineEdit:focus{{border-color:#00c6a7;}}"
        /* Log */
        "QTextEdit#logEdit{{background:#0a0e1a;color:#94a3b8;"
        "  font-family:'Courier New',monospace;font-size:10pt;"
        "  border:1px solid #2a3a50;}}"
        /* GroupBox */
        "QGroupBox{{color:#94a3b8;border:1px solid #2a3a50;"
        "  border-radius:6px;margin-top:8px;padding:8px;}}"
        "QGroupBox::title{{subcontrol-origin:margin;left:10px;padding:0 4px;}}"
        /* ProgressBar */
        "QProgressBar{{border:1px solid #2a3a50;border-radius:4px;"
        "  text-align:center;color:#e2e8f0;}}"
        "QProgressBar::chunk{{background:#00c6a7;border-radius:4px;}}"
        /* ListWidget */
        "QListWidget{{background:#1a2235;color:#e2e8f0;border:1px solid #2a3a50;}}"
        /* ScrollBar */
        "QScrollBar:vertical{{background:#0f1423;width:8px;margin:0;}}"
        "QScrollBar::handle:vertical{{background:#2a3a50;border-radius:4px;}}"
        "QScrollBar::add-line:vertical,QScrollBar::sub-line:vertical{{height:0;}}"
        /* StatusBar */
        "QStatusBar{{background:#0f1423;color:#94a3b8;}}"
    );
}}

/**
 * Show a 1.2s branded splash screen while the app initialises.
 */
static void showSplash(QApplication &app, const QString &project)
{{
    QPixmap pm(540, 280);
    pm.fill(QColor("#070b14"));
    QPainter p(&pm);
    p.setRenderHint(QPainter::Antialiasing);
    p.setPen(QColor("#00c6a7"));
    QFont title("Sans Serif", 22, QFont::Bold);
    p.setFont(title);
    p.drawText(pm.rect().adjusted(40, 60, -40, -160),
               Qt::AlignLeft | Qt::AlignVCenter, project);
    p.setPen(QColor("#94a3b8"));
    QFont sub("Sans Serif", 10);
    p.setFont(sub);
    p.drawText(pm.rect().adjusted(40, 130, -40, -90),
               Qt::AlignLeft | Qt::AlignVCenter,
               "Auto-generated by Silicon to Software (S2S) v2");
    p.setPen(QColor("#64748b"));
    p.drawText(pm.rect().adjusted(40, 200, -40, -40),
               Qt::AlignLeft | Qt::AlignVCenter, "Loading...");
    p.end();
    QSplashScreen splash(pm);
    splash.show();
    app.processEvents();
    QThread::msleep(1200);
    splash.close();
}}

int main(int argc, char *argv[])
{{
    QApplication app(argc, argv);
    app.setApplicationName("{safe}");
    app.setApplicationVersion("1.0");
    app.setOrganizationName("Silicon to Software (S2S) v2");

    applyDarkTheme(app);

    // Branded splash screen.
    showSplash(app, "{project_name}");

    // Mandatory login - on first run a default admin/ChangeMe! account is
    // created and the EventLogger emits a SECURITY warning until the
    // password is rotated.
    EventLogger::instance().info("App", "Application starting");
    UserManager userMgr;
    LoginDialog login(&userMgr);
    if (login.exec() != QDialog::Accepted) {{
        EventLogger::instance().info("App", "Login cancelled - exiting");
        return 0;
    }}
    EventLogger::instance().setCurrentUser(userMgr.currentUser());

    // Force the operator to rotate the bootstrap default password before
    // they reach MainWindow. If they entered any other password the
    // dialog is skipped.
    if (userMgr.lastPassword() == QString("ChangeMe!")) {{
        ForcePasswordChangeDialog pwDlg(&userMgr, userMgr.currentUser());
        if (pwDlg.exec() != QDialog::Accepted) {{
            EventLogger::instance().security(
                "App",
                "User declined to rotate default password - exiting");
            return 0;
        }}
    }}

    MainWindow w;
    w.setWindowTitle("{project_name} - Silicon to Software (S2S) v2  ["
                     + userMgr.currentUser() +
                     (userMgr.isAdmin() ? " | admin]" : " | operator]"));
    w.resize(1280, 800);
    w.show();

    int rc = app.exec();
    EventLogger::instance().info("App", "Application exiting");
    return rc;
}}
"""

    # ------------------------------------------------------------------ #
    # MainWindow.h
    # ------------------------------------------------------------------ #

    def _mainwindow_h(self, project_name: str) -> str:
        return f"""\
#ifndef MAINWINDOW_H
#define MAINWINDOW_H

#include <QMainWindow>
#include <QThread>
#include "SerialWorker.h"

namespace Ui {{ class MainWindow; }}

/**
 * @class MainWindow
 * @brief Application shell for {project_name}.
 *
 * Generated by Silicon to Software (S2S) v2 (Qt 5.14.2, QMake, MinGW).
 *
 * Architecture:
 *   .ui files  — all widget layout/creation (Qt Designer)
 *   Panels     — DashboardPanel / ControlPanel / LogPanel / SettingsPanel
 *   SerialWorker — QThread-based QSerialPort I/O
 *   MainWindow — wires signals between panels and worker; no widget creation
 */
class MainWindow : public QMainWindow
{{
    Q_OBJECT

public:
    explicit MainWindow(QWidget *parent = nullptr);
    ~MainWindow();

private slots:
    /* SerialWorker -> MainWindow */
    void onDataReceived(const QByteArray &data);
    void onSerialError(const QString &error);

    /* SettingsPanel -> MainWindow -> SerialWorker */
    void onConnectRequested(const QString &port, int baud);
    void onDisconnectRequested();

    /* ControlPanel -> MainWindow -> SerialWorker */
    void onCommandSend(const QString &command);

    /* Help menu actions */
    void onAbout();
    void onShowEventLog();

private:
    void initConnections();

    Ui::MainWindow *ui;
    SerialWorker   *m_worker;
    QThread        *m_workerThread;
}};

#endif // MAINWINDOW_H
"""

    # ------------------------------------------------------------------ #
    # MainWindow.cpp
    # ------------------------------------------------------------------ #

    def _mainwindow_cpp(self, project_name: str, design_type: str,
                        peripherals: Optional[List[Dict]] = None,
                        application_class: str = "general") -> str:
        # peripherals/application_class are accepted to keep the
        # signature in sync with generate(). The MainWindow body still
        # emits the standard layout; docking/menu-bar/baseline panels
        # are wired in by the refactor in task #55.
        peripherals = peripherals or []
        _ = application_class  # reserved for the docking refactor
        return f"""\
#include "MainWindow.h"
#include "ui_MainWindow.h"
#include "AboutDialog.h"
#include "EventLogPanel.h"
#include "EventLogger.h"
#include <QDockWidget>
#include <QLabel>
#include <QMessageBox>
#include <QStatusBar>
#include <QTimer>
#include "DashboardPanel.h"
#include "ControlPanel.h"
#include "LogPanel.h"
#include "SettingsPanel.h"
#include <QDateTime>
#include <QStatusBar>

// ======================================================================
// Construction / Destruction
// ======================================================================

MainWindow::MainWindow(QWidget *parent)
    : QMainWindow(parent)
    , ui(new Ui::MainWindow)
    , m_worker(new SerialWorker)
    , m_workerThread(new QThread(this))
{{
    ui->setupUi(this);

    m_worker->moveToThread(m_workerThread);
    connect(m_workerThread, &QThread::finished, m_worker, &QObject::deleteLater);
    m_workerThread->start();

    initConnections();

    statusBar()->showMessage(tr("{project_name} ({design_type}) — Ready"));
}}

MainWindow::~MainWindow()
{{
    m_workerThread->quit();
    m_workerThread->wait(3000);
    delete ui;
}}

// ======================================================================
// Signal wiring — connect() calls only; zero widget creation
// ======================================================================

void MainWindow::initConnections()
{{
    // SerialWorker -> MainWindow
    connect(m_worker, &SerialWorker::dataReceived,
            this,     &MainWindow::onDataReceived);
    connect(m_worker, &SerialWorker::errorOccurred,
            this,     &MainWindow::onSerialError);

    // SettingsPanel -> MainWindow
    connect(ui->settingsPage, &SettingsPanel::connectRequested,
            this,             &MainWindow::onConnectRequested);
    connect(ui->settingsPage, &SettingsPanel::disconnectRequested,
            this,             &MainWindow::onDisconnectRequested);

    // ControlPanel -> MainWindow
    connect(ui->controlPage, &ControlPanel::commandSend,
            this,            &MainWindow::onCommandSend);

    // Help / Tools menu wiring (2026-05-01)
    if (ui->actionAbout)
        connect(ui->actionAbout, &QAction::triggered,
                this, &MainWindow::onAbout);
    if (ui->actionEventLog)
        connect(ui->actionEventLog, &QAction::triggered,
                this, &MainWindow::onShowEventLog);

    // Dockable EventLogPanel - lives in a QDockWidget so the user can
    // detach it / move it around. Hidden by default; shown via menu.
    auto *eventDock = new QDockWidget(tr("Event Log"), this);
    eventDock->setObjectName("eventDock");
    eventDock->setWidget(new EventLogPanel(this));
    eventDock->setAllowedAreas(Qt::BottomDockWidgetArea | Qt::RightDockWidgetArea);
    addDockWidget(Qt::BottomDockWidgetArea, eventDock);
    eventDock->hide();
    // Status bar - live indicators (2026-05-01).
    auto *connLed       = new QLabel(QStringLiteral("● disconnected"));
    connLed->setStyleSheet("color:#dc2626;padding:0 8px;");
    connLed->setObjectName("connLed");
    auto *userLabel     = new QLabel(QStringLiteral("(no user)"));
    userLabel->setStyleSheet("color:#94a3b8;padding:0 8px;");
    auto *lastEventLbl  = new QLabel(QStringLiteral("Ready."));
    lastEventLbl->setStyleSheet("color:#94a3b8;padding:0 8px;");
    lastEventLbl->setObjectName("lastEventLbl");
    auto *heartbeat     = new QLabel(QStringLiteral("●"));
    heartbeat->setStyleSheet("color:#00c6a7;padding:0 8px;");
    heartbeat->setObjectName("heartbeat");
    statusBar()->addPermanentWidget(connLed);
    statusBar()->addPermanentWidget(userLabel);
    statusBar()->addWidget(lastEventLbl, /*stretch=*/1);
    statusBar()->addPermanentWidget(heartbeat);

    auto *hbTimer = new QTimer(this);
    hbTimer->setInterval(1000);
    connect(hbTimer, &QTimer::timeout, this, [heartbeat]() {{
        static bool on = false; on = !on;
        heartbeat->setStyleSheet(on
            ? "color:#00c6a7;padding:0 8px;"
            : "color:#475569;padding:0 8px;");
    }});
    hbTimer->start();

    // Mirror EventLogger -> last-event label.
    connect(&EventLogger::instance(), &EventLogger::eventEmitted, this,
            [lastEventLbl](const QString &iso, EventLogger::Severity sev,
                           const QString &source, const QString &msg,
                           const QString &) {{
                Q_UNUSED(iso);
                QString prefix;
                switch (sev) {{
                    case EventLogger::Severity::Error:    prefix = "[ERR] ";  break;
                    case EventLogger::Severity::Warning:  prefix = "[WARN] "; break;
                    case EventLogger::Severity::Security: prefix = "[SEC] ";  break;
                    default: break;
                }}
                lastEventLbl->setText(QString("%1%2: %3").arg(prefix, source, msg));
            }});

    EventLogger::instance().info("MainWindow", "MainWindow ready");
}}

// ======================================================================
// Slots
// ======================================================================

void MainWindow::onDataReceived(const QByteArray &data)
{{
    ui->dashboardPage->handleIncomingData(data);
    ui->logPage->appendEntry(
        QDateTime::currentDateTime().toString("hh:mm:ss.zzz"),
        "RX",
        QString::fromLatin1(data.toHex(' ').toUpper())
    );
}}

void MainWindow::onSerialError(const QString &error)
{{
    ui->logPage->appendEntry(
        QDateTime::currentDateTime().toString("hh:mm:ss"), "ERR", error);
    ui->settingsPage->setConnectedState(false);
    statusBar()->showMessage(tr("Serial error: %1").arg(error));
}}

void MainWindow::onConnectRequested(const QString &port, int baud)
{{
    QMetaObject::invokeMethod(
        m_worker, "connectPort", Qt::QueuedConnection,
        Q_ARG(QString, port), Q_ARG(int, baud));
    statusBar()->showMessage(tr("Connecting to %1 @ %2 baud\xe2\x80\xa6").arg(port).arg(baud));
    ui->logPage->appendEntry(
        QDateTime::currentDateTime().toString("hh:mm:ss"),
        "INF", tr("Connecting to %1 @ %2").arg(port).arg(baud));
}}

void MainWindow::onDisconnectRequested()
{{
    QMetaObject::invokeMethod(m_worker, "disconnectPort", Qt::QueuedConnection);
    statusBar()->showMessage(tr("Disconnected"));
    ui->settingsPage->setConnectedState(false);
    ui->logPage->appendEntry(
        QDateTime::currentDateTime().toString("hh:mm:ss"), "INF", tr("Disconnected"));
}}

void MainWindow::onAbout()
{{
    AboutDialog dlg(this);
    dlg.exec();
}}

void MainWindow::onShowEventLog()
{{
    auto *dock = findChild<QDockWidget *>("eventDock");
    if (dock) {{
        dock->setVisible(!dock->isVisible());
        EventLogger::instance().info(
            "MainWindow",
            QString("Event Log dock %1").arg(dock->isVisible() ? "shown" : "hidden"));
    }}
}}

void MainWindow::onCommandSend(const QString &command)
{{
    const QByteArray payload = (command + "\\r\\n").toLatin1();
    QMetaObject::invokeMethod(
        m_worker, "sendData", Qt::QueuedConnection,
        Q_ARG(QByteArray, payload));
    ui->logPage->appendEntry(
        QDateTime::currentDateTime().toString("hh:mm:ss"), "TX", command);
}}
"""

    # ------------------------------------------------------------------ #
    # MainWindow.ui  (promoted widgets for each tab)
    # ------------------------------------------------------------------ #

    def _mainwindow_ui(self, safe: str, project_name: str) -> str:
        return f"""\
<?xml version="1.0" encoding="UTF-8"?>
<ui version="4.0">
 <class>MainWindow</class>
 <widget class="QMainWindow" name="MainWindow">
  <property name="geometry">
   <rect><x>0</x><y>0</y><width>1280</width><height>800</height></rect>
  </property>
  <property name="windowTitle">
   <string>{project_name} — Silicon to Software (S2S) v2</string>
  </property>
  <widget class="QWidget" name="centralwidget">
   <layout class="QVBoxLayout" name="centralLayout">
    <property name="spacing"><number>0</number></property>
    <property name="leftMargin"><number>0</number></property>
    <property name="topMargin"><number>0</number></property>
    <property name="rightMargin"><number>0</number></property>
    <property name="bottomMargin"><number>0</number></property>
    <item>
     <widget class="QTabWidget" name="tabWidget">
      <property name="currentIndex"><number>0</number></property>
      <property name="documentMode"><bool>true</bool></property>
      <widget class="WelcomePanel" name="welcomePage">
       <attribute name="title"><string>Welcome</string></attribute>
      </widget>
      <widget class="DashboardPanel" name="dashboardPage">
       <attribute name="title"><string>Dashboard</string></attribute>
      </widget>
      <widget class="ControlPanel" name="controlPage">
       <attribute name="title"><string>Control</string></attribute>
      </widget>
      <widget class="LogPanel" name="logPage">
       <attribute name="title"><string>Log</string></attribute>
      </widget>
      <widget class="SettingsPanel" name="settingsPage">
       <attribute name="title"><string>Settings</string></attribute>
      </widget>
     </widget>
    </item>
   </layout>
  </widget>
  <widget class="QMenuBar" name="menubar">
   <property name="geometry">
    <rect><x>0</x><y>0</y><width>1280</width><height>21</height></rect>
   </property>
   <widget class="QMenu" name="menuFile">
    <property name="title"><string>File</string></property>
    <addaction name="actionOpenConfig"/>
    <addaction name="actionSaveConfig"/>
    <addaction name="separator"/>
    <addaction name="actionExport"/>
    <addaction name="separator"/>
    <addaction name="actionLogout"/>
    <addaction name="actionQuit"/>
   </widget>
   <widget class="QMenu" name="menuView">
    <property name="title"><string>View</string></property>
    <addaction name="actionEventLog"/>
   </widget>
   <widget class="QMenu" name="menuTools">
    <property name="title"><string>Tools</string></property>
    <addaction name="actionUsers"/>
    <addaction name="actionChangePassword"/>
    <addaction name="separator"/>
    <addaction name="actionRunSelfTest"/>
   </widget>
   <widget class="QMenu" name="menuHelp">
    <property name="title"><string>Help</string></property>
    <addaction name="actionUserGuide"/>
    <addaction name="actionAbout"/>
   </widget>
   <addaction name="menuFile"/>
   <addaction name="menuView"/>
   <addaction name="menuTools"/>
   <addaction name="menuHelp"/>
  </widget>
  <widget class="QStatusBar" name="statusbar"/>
  <widget class="QToolBar" name="mainToolBar">
   <property name="windowTitle"><string>Main Toolbar</string></property>
   <attribute name="toolBarArea"><enum>TopToolBarArea</enum></attribute>
   <attribute name="toolBarBreak"><bool>false</bool></attribute>
   <addaction name="actionConnect"/>
   <addaction name="actionDisconnect"/>
   <addaction name="separator"/>
   <addaction name="actionRunSelfTest"/>
   <addaction name="actionExport"/>
   <addaction name="separator"/>
   <addaction name="actionEventLog"/>
  </widget>
  <action name="actionQuit">
   <property name="text"><string>Quit</string></property>
   <property name="shortcut"><string>Ctrl+Q</string></property>
   <property name="toolTip"><string>Exit the application (Ctrl+Q)</string></property>
  </action>
  <action name="actionAbout">
   <property name="text"><string>About</string></property>
   <property name="shortcut"><string>F1</string></property>
   <property name="toolTip"><string>About this application (F1)</string></property>
  </action>
  <action name="actionEventLog">
   <property name="text"><string>Event Log</string></property>
   <property name="shortcut"><string>Ctrl+L</string></property>
   <property name="toolTip"><string>Show / hide the dockable event log (Ctrl+L)</string></property>
  </action>
  <action name="actionUsers">
   <property name="text"><string>Users...</string></property>
   <property name="toolTip"><string>Manage users (admin only)</string></property>
  </action>
  <action name="actionChangePassword">
   <property name="text"><string>Change my password...</string></property>
   <property name="toolTip"><string>Rotate the password for the current user</string></property>
  </action>
  <action name="actionLogout">
   <property name="text"><string>Logout</string></property>
   <property name="toolTip"><string>Log out and return to the login screen</string></property>
  </action>
  <action name="actionOpenConfig">
   <property name="text"><string>Open configuration...</string></property>
   <property name="shortcut"><string>Ctrl+O</string></property>
   <property name="toolTip"><string>Load a saved device configuration (Ctrl+O)</string></property>
  </action>
  <action name="actionSaveConfig">
   <property name="text"><string>Save configuration...</string></property>
   <property name="shortcut"><string>Ctrl+S</string></property>
   <property name="toolTip"><string>Save the current device configuration (Ctrl+S)</string></property>
  </action>
  <action name="actionExport">
   <property name="text"><string>Export current view...</string></property>
   <property name="shortcut"><string>Ctrl+E</string></property>
   <property name="toolTip"><string>Export the visible panel data to CSV (Ctrl+E)</string></property>
  </action>
  <action name="actionConnect">
   <property name="text"><string>Connect</string></property>
   <property name="toolTip"><string>Open the device connection</string></property>
  </action>
  <action name="actionDisconnect">
   <property name="text"><string>Disconnect</string></property>
   <property name="toolTip"><string>Close the device connection</string></property>
  </action>
  <action name="actionRunSelfTest">
   <property name="text"><string>Run self-test</string></property>
   <property name="shortcut"><string>F5</string></property>
   <property name="toolTip"><string>Run the on-board self-test suite (F5)</string></property>
  </action>
  <action name="actionUserGuide">
   <property name="text"><string>User guide</string></property>
   <property name="toolTip"><string>Open the bundled user guide PDF</string></property>
  </action>
 </widget>
 <customwidgets>
  <customwidget>
   <class>WelcomePanel</class>
   <extends>QWidget</extends>
   <header>WelcomePanel.h</header>
  </customwidget>
  <customwidget>
   <class>DashboardPanel</class>
   <extends>QWidget</extends>
   <header>DashboardPanel.h</header>
  </customwidget>
  <customwidget>
   <class>ControlPanel</class>
   <extends>QWidget</extends>
   <header>ControlPanel.h</header>
  </customwidget>
  <customwidget>
   <class>LogPanel</class>
   <extends>QWidget</extends>
   <header>LogPanel.h</header>
  </customwidget>
  <customwidget>
   <class>SettingsPanel</class>
   <extends>QWidget</extends>
   <header>SettingsPanel.h</header>
  </customwidget>
 </customwidgets>
 <resources/>
 <connections/>
</ui>
"""

    # ------------------------------------------------------------------ #
    # SerialWorker.h / .cpp
    # ------------------------------------------------------------------ #

    def _serialworker_h(self) -> str:
        return """\
#ifndef SERIALWORKER_H
#define SERIALWORKER_H

#include <QObject>
#include <QSerialPort>

/**
 * @class SerialWorker
 * @brief QThread-based QSerialPort I/O worker.
 *
 * Move to a QThread, then call slots via QMetaObject::invokeMethod with
 * Qt::QueuedConnection to ensure thread-safe access.
 *
 * Newline-framed packet protocol: emits dataReceived() for each '\\n'-
 * terminated packet received. Adapt framing logic for your hardware protocol.
 */
class SerialWorker : public QObject
{
    Q_OBJECT

public:
    explicit SerialWorker(QObject *parent = nullptr);
    ~SerialWorker() Q_DECL_OVERRIDE;

public slots:
    void connectPort(const QString &portName, int baudRate);
    void disconnectPort();
    void sendData(const QByteArray &data);

signals:
    void dataReceived(const QByteArray &packet);
    void errorOccurred(const QString &message);

private slots:
    void onReadyRead();
    void onErrorOccurred(QSerialPort::SerialPortError error);

private:
    QSerialPort *m_port;
    QByteArray   m_buffer;
};

#endif // SERIALWORKER_H
"""

    def _serialworker_cpp(self) -> str:
        return """\
#include "SerialWorker.h"

SerialWorker::SerialWorker(QObject *parent)
    : QObject(parent)
    , m_port(new QSerialPort(this))
{
    connect(m_port, &QSerialPort::readyRead,
            this,   &SerialWorker::onReadyRead);
    connect(m_port, &QSerialPort::errorOccurred,
            this,   &SerialWorker::onErrorOccurred);
}

SerialWorker::~SerialWorker()
{
    disconnectPort();
}

void SerialWorker::connectPort(const QString &portName, int baudRate)
{
    if (m_port->isOpen())
        m_port->close();

    m_port->setPortName(portName);
    m_port->setBaudRate(static_cast<QSerialPort::BaudRate>(baudRate));
    m_port->setDataBits(QSerialPort::Data8);
    m_port->setParity(QSerialPort::NoParity);
    m_port->setStopBits(QSerialPort::OneStop);
    m_port->setFlowControl(QSerialPort::NoFlowControl);

    if (!m_port->open(QIODevice::ReadWrite))
        emit errorOccurred(QString("Cannot open %1: %2")
                           .arg(portName, m_port->errorString()));
}

void SerialWorker::disconnectPort()
{
    if (m_port && m_port->isOpen()) {
        m_port->flush();
        m_port->close();
    }
    m_buffer.clear();
}

void SerialWorker::sendData(const QByteArray &data)
{
    if (m_port && m_port->isOpen())
        m_port->write(data);
}

void SerialWorker::onReadyRead()
{
    m_buffer.append(m_port->readAll());
    int idx;
    while ((idx = m_buffer.indexOf('\\n')) != -1) {
        const QByteArray pkt = m_buffer.left(idx + 1);
        m_buffer.remove(0, idx + 1);
        if (!pkt.trimmed().isEmpty())
            emit dataReceived(pkt);
    }
}

void SerialWorker::onErrorOccurred(QSerialPort::SerialPortError error)
{
    if (error != QSerialPort::NoError)
        emit errorOccurred(m_port->errorString());
}
"""

    # ------------------------------------------------------------------ #
    # DashboardPanel
    # ------------------------------------------------------------------ #

    def _dashboard_h(self) -> str:
        return """\
#ifndef DASHBOARDPANEL_H
#define DASHBOARDPANEL_H

#include <QWidget>

namespace Ui { class DashboardPanel; }

/**
 * @class DashboardPanel
 * @brief Live-data dashboard: stat cards, data table, progress bar.
 * Layout defined entirely in DashboardPanel.ui (Qt Designer).
 */
class DashboardPanel : public QWidget
{
    Q_OBJECT
public:
    explicit DashboardPanel(QWidget *parent = nullptr);
    ~DashboardPanel();

    /** Called by MainWindow on every incoming serial packet. */
    void handleIncomingData(const QByteArray &data);

private:
    Ui::DashboardPanel *ui;
    int m_packetCount;
};

#endif // DASHBOARDPANEL_H
"""

    def _dashboard_cpp(self, project_name: str) -> str:
        return f"""\
#include "DashboardPanel.h"
#include "ui_DashboardPanel.h"
#include <QDateTime>
#include <QTableWidgetItem>
#include <QHeaderView>

DashboardPanel::DashboardPanel(QWidget *parent)
    : QWidget(parent)
    , ui(new Ui::DashboardPanel)
    , m_packetCount(0)
{{
    ui->setupUi(this);

    /* Configure table — column sizing only, no new widgets */
    ui->dataTable->horizontalHeader()->setStretchLastSection(true);
    ui->dataTable->horizontalHeader()->setSectionResizeMode(QHeaderView::Stretch);
    ui->dataTable->setEditTriggers(QAbstractItemView::NoEditTriggers);
    ui->dataTable->setSelectionBehavior(QAbstractItemView::SelectRows);
    ui->dataTable->setAlternatingRowColors(true);

    ui->progressBar->setRange(0, 100);
    ui->progressBar->setValue(0);
    ui->progressBar->setFormat("%p%  buffer");

    ui->headerLabel->setText(tr("<b>{project_name} &amp;mdash; Live Dashboard</b>"));
}}

DashboardPanel::~DashboardPanel()
{{
    delete ui;
}}

void DashboardPanel::handleIncomingData(const QByteArray &data)
{{
    ++m_packetCount;

    /* Update stat cards via ui-> (no new widgets) */
    ui->stat1Value->setText(QString::number(m_packetCount));
    ui->progressBar->setValue(m_packetCount % 101);

    /* Append table row; cap at 200 rows */
    if (ui->dataTable->rowCount() >= 200)
        ui->dataTable->removeRow(0);

    const int row = ui->dataTable->rowCount();
    ui->dataTable->insertRow(row);
    ui->dataTable->setItem(row, 0, new QTableWidgetItem(
        QDateTime::currentDateTime().toString("hh:mm:ss.zzz")));
    ui->dataTable->setItem(row, 1, new QTableWidgetItem("CH0"));
    ui->dataTable->setItem(row, 2, new QTableWidgetItem(
        QString::fromLatin1(data.toHex(' ').toUpper()).left(64)));
    ui->dataTable->setItem(row, 3, new QTableWidgetItem("raw"));
    ui->dataTable->scrollToBottom();
}}
"""

    def _dashboard_ui(self, project_name: str) -> str:
        return f"""\
<?xml version="1.0" encoding="UTF-8"?>
<ui version="4.0">
 <class>DashboardPanel</class>
 <widget class="QWidget" name="DashboardPanel">
  <property name="geometry">
   <rect><x>0</x><y>0</y><width>900</width><height>700</height></rect>
  </property>
  <layout class="QVBoxLayout" name="outerLayout">
   <property name="leftMargin"><number>16</number></property>
   <property name="topMargin"><number>16</number></property>
   <property name="rightMargin"><number>16</number></property>
   <property name="bottomMargin"><number>16</number></property>
   <property name="spacing"><number>10</number></property>
   <!-- Header label -->
   <item>
    <widget class="QLabel" name="headerLabel">
     <property name="text"><string>{project_name} &amp;mdash; Live Dashboard</string></property>
     <property name="styleSheet">
      <string>color:#00c6a7; font-size:15px; font-weight:bold;</string>
     </property>
    </widget>
   </item>
   <!-- Stat cards row -->
   <item>
    <layout class="QHBoxLayout" name="statsRow">
     <property name="spacing"><number>10</number></property>
     <item>
      <widget class="QGroupBox" name="stat1Box">
       <property name="title"><string>Packets Received</string></property>
       <layout class="QVBoxLayout" name="lay1">
        <item>
         <widget class="QLabel" name="stat1Value">
          <property name="text"><string>0</string></property>
          <property name="alignment"><set>Qt::AlignCenter</set></property>
          <property name="styleSheet">
           <string>font-size:24px; font-weight:bold; color:#00c6a7;</string>
          </property>
         </widget>
        </item>
       </layout>
      </widget>
     </item>
     <item>
      <widget class="QGroupBox" name="stat2Box">
       <property name="title"><string>Voltage Rail</string></property>
       <layout class="QVBoxLayout" name="lay2">
        <item>
         <widget class="QLabel" name="stat2Value">
          <property name="text"><string>0.00 V</string></property>
          <property name="alignment"><set>Qt::AlignCenter</set></property>
          <property name="styleSheet">
           <string>font-size:24px; font-weight:bold; color:#3b82f6;</string>
          </property>
         </widget>
        </item>
       </layout>
      </widget>
     </item>
     <item>
      <widget class="QGroupBox" name="stat3Box">
       <property name="title"><string>Sample Rate</string></property>
       <layout class="QVBoxLayout" name="lay3">
        <item>
         <widget class="QLabel" name="stat3Value">
          <property name="text"><string>0 Hz</string></property>
          <property name="alignment"><set>Qt::AlignCenter</set></property>
          <property name="styleSheet">
           <string>font-size:24px; font-weight:bold; color:#f59e0b;</string>
          </property>
         </widget>
        </item>
       </layout>
      </widget>
     </item>
     <item>
      <widget class="QGroupBox" name="stat4Box">
       <property name="title"><string>Errors</string></property>
       <layout class="QVBoxLayout" name="lay4">
        <item>
         <widget class="QLabel" name="stat4Value">
          <property name="text"><string>0</string></property>
          <property name="alignment"><set>Qt::AlignCenter</set></property>
          <property name="styleSheet">
           <string>font-size:24px; font-weight:bold; color:#dc2626;</string>
          </property>
         </widget>
        </item>
       </layout>
      </widget>
     </item>
    </layout>
   </item>
   <!-- Data table -->
   <item>
    <widget class="QTableWidget" name="dataTable">
     <property name="columnCount"><number>4</number></property>
     <attribute name="horizontalHeaderStretchLastSection">
      <bool>true</bool>
     </attribute>
     <column><property name="text"><string>Timestamp</string></property></column>
     <column><property name="text"><string>Channel</string></property></column>
     <column><property name="text"><string>Value</string></property></column>
     <column><property name="text"><string>Unit</string></property></column>
    </widget>
   </item>
   <!-- Progress bar -->
   <item>
    <widget class="QProgressBar" name="progressBar">
     <property name="value"><number>0</number></property>
     <property name="textVisible"><bool>true</bool></property>
    </widget>
   </item>
  </layout>
 </widget>
 <resources/>
 <connections/>
</ui>
"""

    # ------------------------------------------------------------------ #
    # ControlPanel
    # ------------------------------------------------------------------ #

    def _control_h(self) -> str:
        return """\
#ifndef CONTROLPANEL_H
#define CONTROLPANEL_H

#include <QWidget>

namespace Ui { class ControlPanel; }

/**
 * @class ControlPanel
 * @brief Manual command-entry panel with history list.
 * Layout defined in ControlPanel.ui.
 */
class ControlPanel : public QWidget
{
    Q_OBJECT
public:
    explicit ControlPanel(QWidget *parent = nullptr);
    ~ControlPanel();

signals:
    void commandSend(const QString &command);

private slots:
    void onSendClicked();

private:
    Ui::ControlPanel *ui;
};

#endif // CONTROLPANEL_H
"""

    def _control_cpp(self) -> str:
        return """\
#include "ControlPanel.h"
#include "ui_ControlPanel.h"

ControlPanel::ControlPanel(QWidget *parent)
    : QWidget(parent)
    , ui(new Ui::ControlPanel)
{
    ui->setupUi(this);
    connect(ui->sendButton,  &QPushButton::clicked,
            this,            &ControlPanel::onSendClicked);
    connect(ui->commandEdit, &QLineEdit::returnPressed,
            this,            &ControlPanel::onSendClicked);
}

ControlPanel::~ControlPanel()
{
    delete ui;
}

void ControlPanel::onSendClicked()
{
    const QString cmd = ui->commandEdit->text().trimmed();
    if (cmd.isEmpty())
        return;

    /* Prepend to history list — existing widget, no new widget created */
    ui->historyList->insertItem(0, cmd);
    if (ui->historyList->count() > 50)
        delete ui->historyList->takeItem(ui->historyList->count() - 1);

    ui->commandEdit->clear();
    emit commandSend(cmd);
}
"""

    def _control_ui(self) -> str:
        return """\
<?xml version="1.0" encoding="UTF-8"?>
<ui version="4.0">
 <class>ControlPanel</class>
 <widget class="QWidget" name="ControlPanel">
  <property name="geometry">
   <rect><x>0</x><y>0</y><width>900</width><height>700</height></rect>
  </property>
  <layout class="QVBoxLayout" name="outerLayout">
   <property name="leftMargin"><number>16</number></property>
   <property name="topMargin"><number>16</number></property>
   <property name="rightMargin"><number>16</number></property>
   <property name="bottomMargin"><number>16</number></property>
   <property name="spacing"><number>12</number></property>
   <item>
    <widget class="QLabel" name="headerLabel">
     <property name="text"><string>&lt;b&gt;Manual Control&lt;/b&gt;</string></property>
     <property name="styleSheet">
      <string>color:#00c6a7; font-size:14px;</string>
     </property>
    </widget>
   </item>
   <item>
    <widget class="QGroupBox" name="commandGroup">
     <property name="title"><string>Send Command</string></property>
     <layout class="QHBoxLayout" name="cmdRow">
      <item>
       <widget class="QLineEdit" name="commandEdit">
        <property name="placeholderText">
         <string>Enter command (e.g. READ_REG 0x10)</string>
        </property>
       </widget>
      </item>
      <item>
       <widget class="QPushButton" name="sendButton">
        <property name="objectName"><string>sendButton</string></property>
        <property name="text"><string>Send</string></property>
        <property name="maximumWidth"><number>90</number></property>
       </widget>
      </item>
     </layout>
    </widget>
   </item>
   <item>
    <widget class="QLabel" name="historyLabel">
     <property name="text"><string>Command History (most recent first):</string></property>
     <property name="styleSheet"><string>color:#94a3b8; font-size:12px;</string></property>
    </widget>
   </item>
   <item>
    <widget class="QListWidget" name="historyList"/>
   </item>
   <item>
    <spacer name="vSpacer">
     <property name="orientation"><enum>Qt::Vertical</enum></property>
     <property name="sizeHint" stdset="0">
      <size><width>20</width><height>20</height></size>
     </property>
    </spacer>
   </item>
  </layout>
 </widget>
 <resources/>
 <connections/>
</ui>
"""

    # ------------------------------------------------------------------ #
    # LogPanel
    # ------------------------------------------------------------------ #

    def _log_h(self) -> str:
        return """\
#ifndef LOGPANEL_H
#define LOGPANEL_H

#include <QWidget>

namespace Ui { class LogPanel; }

/**
 * @class LogPanel
 * @brief Colour-coded scrolling serial log.
 * Layout defined in LogPanel.ui.
 */
class LogPanel : public QWidget
{
    Q_OBJECT
public:
    explicit LogPanel(QWidget *parent = nullptr);
    ~LogPanel();

    /**
     * Append a colour-coded entry.
     * @param timestamp  hh:mm:ss.zzz string
     * @param type       "RX" | "TX" | "ERR" | "INF"
     * @param message    payload text
     */
    void appendEntry(const QString &timestamp,
                     const QString &type,
                     const QString &message);

private slots:
    void onClearClicked();

private:
    Ui::LogPanel *ui;
};

#endif // LOGPANEL_H
"""

    def _log_cpp(self) -> str:
        return """\
#include "LogPanel.h"
#include "ui_LogPanel.h"

LogPanel::LogPanel(QWidget *parent)
    : QWidget(parent)
    , ui(new Ui::LogPanel)
{
    ui->setupUi(this);
    connect(ui->clearButton, &QPushButton::clicked,
            this,            &LogPanel::onClearClicked);
}

LogPanel::~LogPanel()
{
    delete ui;
}

void LogPanel::appendEntry(const QString &timestamp,
                            const QString &type,
                            const QString &message)
{
    /* Colour per type — using existing ui->logEdit; no new widget */
    QString colour = "#94a3b8";
    if      (type == "RX")  colour = "#00c6a7";
    else if (type == "TX")  colour = "#3b82f6";
    else if (type == "ERR") colour = "#dc2626";
    else if (type == "INF") colour = "#f59e0b";

    const QString html = QString(
        "<span style=\\"color:#64748b\\">[%1]</span> "
        "<span style=\\"color:%2;font-weight:bold\\">[%3]</span> "
        "<span style=\\"color:#e2e8f0\\">%4</span>"
    ).arg(timestamp, colour, type, message.toHtmlEscaped());

    ui->logEdit->append(html);
}

void LogPanel::onClearClicked()
{
    ui->logEdit->clear();
}
"""

    def _log_ui(self) -> str:
        return """\
<?xml version="1.0" encoding="UTF-8"?>
<ui version="4.0">
 <class>LogPanel</class>
 <widget class="QWidget" name="LogPanel">
  <property name="geometry">
   <rect><x>0</x><y>0</y><width>900</width><height>700</height></rect>
  </property>
  <layout class="QVBoxLayout" name="outerLayout">
   <property name="leftMargin"><number>16</number></property>
   <property name="topMargin"><number>12</number></property>
   <property name="rightMargin"><number>16</number></property>
   <property name="bottomMargin"><number>12</number></property>
   <property name="spacing"><number>8</number></property>
   <!-- Toolbar: title + spacer + clear button -->
   <item>
    <layout class="QHBoxLayout" name="toolbarRow">
     <item>
      <widget class="QLabel" name="titleLabel">
       <property name="text"><string>&lt;b&gt;Serial Log&lt;/b&gt;</string></property>
       <property name="styleSheet"><string>color:#00c6a7; font-size:13px;</string></property>
      </widget>
     </item>
     <item>
      <spacer name="hSpacer">
       <property name="orientation"><enum>Qt::Horizontal</enum></property>
       <property name="sizeHint" stdset="0">
        <size><width>40</width><height>20</height></size>
       </property>
      </spacer>
     </item>
     <item>
      <widget class="QPushButton" name="clearButton">
       <property name="text"><string>Clear</string></property>
       <property name="maximumWidth"><number>70</number></property>
      </widget>
     </item>
    </layout>
   </item>
   <!-- Log text area -->
   <item>
    <widget class="QTextEdit" name="logEdit">
     <property name="objectName"><string>logEdit</string></property>
     <property name="readOnly"><bool>true</bool></property>
     <property name="lineWrapMode"><enum>QTextEdit::NoWrap</enum></property>
     <property name="styleSheet">
      <string>background:#0a0e1a; color:#94a3b8; font-family:'Courier New',monospace; font-size:10pt; border:1px solid #2a3a50;</string>
     </property>
    </widget>
   </item>
  </layout>
 </widget>
 <resources/>
 <connections/>
</ui>
"""

    # ------------------------------------------------------------------ #
    # SettingsPanel
    # ------------------------------------------------------------------ #

    def _settings_h(self) -> str:
        return """\
#ifndef SETTINGSPANEL_H
#define SETTINGSPANEL_H

#include <QWidget>

namespace Ui { class SettingsPanel; }

/**
 * @class SettingsPanel
 * @brief Serial port configuration (port, baud, data bits, parity, stop bits).
 * Layout defined in SettingsPanel.ui.
 */
class SettingsPanel : public QWidget
{
    Q_OBJECT
public:
    explicit SettingsPanel(QWidget *parent = nullptr);
    ~SettingsPanel();

    /** Reflect connected/disconnected state in UI (buttons, status label). */
    void setConnectedState(bool connected);

signals:
    void connectRequested(const QString &port, int baud);
    void disconnectRequested();

private slots:
    void onConnectClicked();
    void onDisconnectClicked();
    void onRefreshClicked();

private:
    void populatePorts();
    Ui::SettingsPanel *ui;
};

#endif // SETTINGSPANEL_H
"""

    def _settings_cpp(self) -> str:
        return """\
#include "SettingsPanel.h"
#include "ui_SettingsPanel.h"
#include <QSerialPortInfo>

SettingsPanel::SettingsPanel(QWidget *parent)
    : QWidget(parent)
    , ui(new Ui::SettingsPanel)
{
    ui->setupUi(this);

    populatePorts();

    /* Set default baud rate — existing widget, data-only change */
    ui->baudCombo->setCurrentText("115200");

    connect(ui->connectButton,    &QPushButton::clicked,
            this,                 &SettingsPanel::onConnectClicked);
    connect(ui->disconnectButton, &QPushButton::clicked,
            this,                 &SettingsPanel::onDisconnectClicked);
    connect(ui->refreshButton,    &QPushButton::clicked,
            this,                 &SettingsPanel::onRefreshClicked);

    ui->disconnectButton->setEnabled(false);
    ui->statusLabel->setText(tr("Not connected"));
    ui->statusLabel->setStyleSheet("color:#dc2626; font-weight:bold;");
}

SettingsPanel::~SettingsPanel()
{
    delete ui;
}

void SettingsPanel::setConnectedState(bool connected)
{
    ui->connectButton->setEnabled(!connected);
    ui->disconnectButton->setEnabled(connected);
    ui->portCombo->setEnabled(!connected);
    ui->baudCombo->setEnabled(!connected);

    if (connected) {
        ui->statusLabel->setText(
            tr("Connected: %1").arg(ui->portCombo->currentText()));
        ui->statusLabel->setStyleSheet("color:#00c6a7; font-weight:bold;");
    } else {
        ui->statusLabel->setText(tr("Not connected"));
        ui->statusLabel->setStyleSheet("color:#dc2626; font-weight:bold;");
    }
}

void SettingsPanel::onConnectClicked()
{
    emit connectRequested(
        ui->portCombo->currentText(),
        ui->baudCombo->currentText().toInt());
}

void SettingsPanel::onDisconnectClicked()
{
    emit disconnectRequested();
}

void SettingsPanel::onRefreshClicked()
{
    populatePorts();
}

void SettingsPanel::populatePorts()
{
    const QString prev = ui->portCombo->currentText();
    ui->portCombo->clear();

    /* Populate from QSerialPortInfo — data only, no new widgets */
    const QList<QSerialPortInfo> ports = QSerialPortInfo::availablePorts();
    for (const QSerialPortInfo &info : ports)
        ui->portCombo->addItem(info.portName());

    if (ui->portCombo->count() == 0)
        ui->portCombo->addItem(tr("No ports found"));

    const int idx = ui->portCombo->findText(prev);
    if (idx >= 0)
        ui->portCombo->setCurrentIndex(idx);
}
"""

    def _settings_ui(self) -> str:
        return """\
<?xml version="1.0" encoding="UTF-8"?>
<ui version="4.0">
 <class>SettingsPanel</class>
 <widget class="QWidget" name="SettingsPanel">
  <property name="geometry">
   <rect><x>0</x><y>0</y><width>900</width><height>700</height></rect>
  </property>
  <layout class="QVBoxLayout" name="outerLayout">
   <property name="leftMargin"><number>16</number></property>
   <property name="topMargin"><number>16</number></property>
   <property name="rightMargin"><number>16</number></property>
   <property name="bottomMargin"><number>16</number></property>
   <property name="spacing"><number>14</number></property>
   <item>
    <widget class="QLabel" name="headerLabel">
     <property name="text"><string>&lt;b&gt;Connection Settings&lt;/b&gt;</string></property>
     <property name="styleSheet"><string>color:#00c6a7; font-size:14px;</string></property>
    </widget>
   </item>
   <item>
    <widget class="QGroupBox" name="portGroup">
     <property name="title"><string>Serial Port</string></property>
     <layout class="QFormLayout" name="portForm">
      <item row="0" column="0">
       <widget class="QLabel" name="lPort"><property name="text"><string>Port:</string></property></widget>
      </item>
      <item row="0" column="1">
       <widget class="QComboBox" name="portCombo">
        <property name="minimumWidth"><number>160</number></property>
       </widget>
      </item>
      <item row="1" column="0">
       <widget class="QLabel" name="lBaud"><property name="text"><string>Baud Rate:</string></property></widget>
      </item>
      <item row="1" column="1">
       <widget class="QComboBox" name="baudCombo">
        <item><property name="text"><string>9600</string></property></item>
        <item><property name="text"><string>19200</string></property></item>
        <item><property name="text"><string>38400</string></property></item>
        <item><property name="text"><string>57600</string></property></item>
        <item><property name="text"><string>115200</string></property></item>
        <item><property name="text"><string>230400</string></property></item>
        <item><property name="text"><string>460800</string></property></item>
        <item><property name="text"><string>921600</string></property></item>
       </widget>
      </item>
      <item row="2" column="0">
       <widget class="QLabel" name="lData"><property name="text"><string>Data Bits:</string></property></widget>
      </item>
      <item row="2" column="1">
       <widget class="QComboBox" name="dataBitsCombo">
        <item><property name="text"><string>8</string></property></item>
        <item><property name="text"><string>7</string></property></item>
        <item><property name="text"><string>6</string></property></item>
        <item><property name="text"><string>5</string></property></item>
       </widget>
      </item>
      <item row="3" column="0">
       <widget class="QLabel" name="lParity"><property name="text"><string>Parity:</string></property></widget>
      </item>
      <item row="3" column="1">
       <widget class="QComboBox" name="parityCombo">
        <item><property name="text"><string>None</string></property></item>
        <item><property name="text"><string>Even</string></property></item>
        <item><property name="text"><string>Odd</string></property></item>
        <item><property name="text"><string>Mark</string></property></item>
        <item><property name="text"><string>Space</string></property></item>
       </widget>
      </item>
      <item row="4" column="0">
       <widget class="QLabel" name="lStop"><property name="text"><string>Stop Bits:</string></property></widget>
      </item>
      <item row="4" column="1">
       <widget class="QComboBox" name="stopBitsCombo">
        <item><property name="text"><string>1</string></property></item>
        <item><property name="text"><string>1.5</string></property></item>
        <item><property name="text"><string>2</string></property></item>
       </widget>
      </item>
     </layout>
    </widget>
   </item>
   <!-- Buttons + status -->
   <item>
    <layout class="QHBoxLayout" name="btnRow">
     <property name="spacing"><number>10</number></property>
     <item>
      <widget class="QPushButton" name="connectButton">
       <property name="objectName"><string>connectButton</string></property>
       <property name="text"><string>Connect</string></property>
      </widget>
     </item>
     <item>
      <widget class="QPushButton" name="disconnectButton">
       <property name="objectName"><string>disconnectButton</string></property>
       <property name="text"><string>Disconnect</string></property>
      </widget>
     </item>
     <item>
      <widget class="QPushButton" name="refreshButton">
       <property name="text"><string>Refresh Ports</string></property>
      </widget>
     </item>
     <item>
      <spacer name="hSpacer">
       <property name="orientation"><enum>Qt::Horizontal</enum></property>
       <property name="sizeHint" stdset="0">
        <size><width>40</width><height>20</height></size>
       </property>
      </spacer>
     </item>
     <item>
      <widget class="QLabel" name="statusLabel">
       <property name="text"><string>Not connected</string></property>
       <property name="styleSheet">
        <string>color:#dc2626; font-weight:bold;</string>
       </property>
      </widget>
     </item>
    </layout>
   </item>
   <item>
    <spacer name="vSpacer">
     <property name="orientation"><enum>Qt::Vertical</enum></property>
     <property name="sizeHint" stdset="0">
      <size><width>20</width><height>40</height></size>
     </property>
    </spacer>
   </item>
  </layout>
 </widget>
 <resources/>
 <connections/>
</ui>
"""

    # ------------------------------------------------------------------ #
    # Helpers
    # ------------------------------------------------------------------ #

    @staticmethod
    def _safe_class_name(name: str) -> str:
        """Convert project name to valid C++ identifier / QMake target name."""
        import re
        safe = re.sub(r"[^a-zA-Z0-9]+", "_", name).strip("_")
        if safe and safe[0].isdigit():
            safe = "Project_" + safe
        return safe or "HardwarePipeline"
