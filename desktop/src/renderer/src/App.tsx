import { ApprovalPopup } from './components/ApprovalPopup'
import { MainWindow } from './components/MainWindow'

export function App() {
  // The main process loads the same bundle with #main or #popup.
  return window.location.hash === '#popup' ? <ApprovalPopup /> : <MainWindow />
}
