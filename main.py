# main.py
import sys
from PyQt5.QtWidgets import QApplication
from gui.main_window import CADMainWindow

def main():
    app = QApplication(sys.argv)
    w = CADMainWindow()
    w.show()
    sys.exit(app.exec_())

if __name__ == "__main__":
    main()
