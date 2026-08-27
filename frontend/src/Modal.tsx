export default function Modal({
  title,
  onClose,
  children,
  foot,
}: {
  title: string
  onClose: () => void
  children: React.ReactNode
  foot?: React.ReactNode
}) {
  return (
    <div className="modal-backdrop open" onClick={onClose}>
      <div className="modal" role="dialog" aria-modal="true" onClick={(e) => e.stopPropagation()}>
        <div className="modal-head">
          <span className="modal-title">{title}</span>
          <button className="modal-close" aria-label="Close" onClick={onClose}>
            &times;
          </button>
        </div>
        <div className="modal-body">{children}</div>
        {foot && <div className="modal-foot">{foot}</div>}
      </div>
    </div>
  )
}
