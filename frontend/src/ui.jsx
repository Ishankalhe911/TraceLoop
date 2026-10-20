import React, { useEffect, useId, useRef } from 'react';
import { Check, Copy, AlertCircle, X, LoaderCircle, ChevronDown, ArrowUpRight } from 'lucide-react';

export function Button({ children, variant = 'primary', size = 'md', loading = false, disabled = false, icon: Icon, className = '', ...props }) {
  return (
    <button className={`button button-${variant} button-${size} ${className}`} disabled={disabled || loading} {...props}>
      {loading ? <LoaderCircle size={16} className="spin" /> : Icon ? <Icon size={16} strokeWidth={2} /> : null}
      <span>{children}</span>
    </button>
  );
}

export const IconButton = React.forwardRef(function IconButton({ label, children, className = '', ...props }, ref) {
  return <button ref={ref} className={`icon-button ${className}`} aria-label={label} title={label} {...props}>{children}</button>;
});

export function CopyButton({ value, onCopy, className = '' }) {
  const handleCopy = async () => {
    try {
      const { copyText } = await import('./api.js');
      await copyText(value);
      onCopy?.('Copied to clipboard.');
    } catch (error) {
      onCopy?.(error.message || 'Unable to copy.');
    }
  };
  return <IconButton className={`copy-button ${className}`} label="Copy ID" onClick={handleCopy}><Copy size={15} /></IconButton>;
}

export function Badge({ children, tone = 'neutral' }) {
  return <span className={`badge badge-${tone}`}>{children}</span>;
}

export function Field({ label, help, error, children, className = '', htmlFor }) {
  return (
    <div className={`field ${className}`}>
      {label ? <label className="field-label" htmlFor={htmlFor}>{label}</label> : null}
      {children}
      {help ? <small className="field-help">{help}</small> : null}
      {error ? <small className="field-error">{error}</small> : null}
    </div>
  );
}

export function TextInput({ label, help, error, className = '', id: suppliedId, ...props }) {
  const generatedId = useId();
  const id = suppliedId || generatedId;
  return <Field label={label} htmlFor={id} help={help} error={error} className={className}><input id={id} className="input" {...props} /></Field>;
}

export function SelectInput({ label, help, error, children, className = '', id: suppliedId, ...props }) {
  const generatedId = useId();
  const id = suppliedId || generatedId;
  return <Field label={label} htmlFor={id} help={help} error={error} className={className}><div className="select-wrap"><select id={id} className="input select" {...props}>{children}</select><ChevronDown size={16} /></div></Field>;
}

export function TextArea({ label, help, error, className = '', id: suppliedId, ...props }) {
  const generatedId = useId();
  const id = suppliedId || generatedId;
  return <Field label={label} htmlFor={id} help={help} error={error} className={className}><textarea id={id} className="input textarea" {...props} /></Field>;
}

export function Panel({ title, description, actions, children, className = '' }) {
  return (
    <section className={`panel ${className}`}>
      {(title || description || actions) && <div className="panel-head">
        <div>{title ? <h2>{title}</h2> : null}{description ? <p>{description}</p> : null}</div>
        {actions ? <div className="panel-actions">{actions}</div> : null}
      </div>}
      {children}
    </section>
  );
}

export function EmptyState({ icon: Icon = AlertCircle, title, description, action }) {
  return <div className="empty-state"><span className="empty-icon"><Icon size={22} /></span><h3>{title}</h3><p>{description}</p>{action}</div>;
}

export function Notice({ kind = 'info', children, onClose }) {
  const Icon = kind === 'success' ? Check : AlertCircle;
  return <div className={`notice notice-${kind}`} role={kind === 'error' ? 'alert' : 'status'}><Icon size={17} /><div>{children}</div>{onClose && <IconButton label="Dismiss" onClick={onClose}><X size={15} /></IconButton>}</div>;
}

export function Modal({ open, title, description, children, onClose, size = 'md' }) {
  const closeRef = useRef(null);
  const dialogRef = useRef(null);
  const closeHandlerRef = useRef(onClose);

  useEffect(() => {
    closeHandlerRef.current = onClose;
  }, [onClose]);

  useEffect(() => {
    if (!open) return undefined;
    const previousFocus = document.activeElement;
    closeRef.current?.focus();

    const onKeyDown = (event) => {
      if (event.key === 'Escape') {
        event.preventDefault();
        closeHandlerRef.current?.();
        return;
      }

      if (event.key !== 'Tab' || !dialogRef.current) return;
      const focusable = dialogRef.current.querySelectorAll(
        'button:not([disabled]), a[href], input:not([disabled]), select:not([disabled]), textarea:not([disabled]), [tabindex]:not([tabindex="-1"])'
      );
      if (!focusable.length) return;
      const first = focusable[0];
      const last = focusable[focusable.length - 1];
      if (event.shiftKey && document.activeElement === first) {
        event.preventDefault();
        last.focus();
      } else if (!event.shiftKey && document.activeElement === last) {
        event.preventDefault();
        first.focus();
      }
    };

    document.addEventListener('keydown', onKeyDown);
    document.body.classList.add('modal-open');
    return () => {
      document.removeEventListener('keydown', onKeyDown);
      document.body.classList.remove('modal-open');
      previousFocus?.focus?.();
    };
  }, [open]);
  if (!open) return null;
  return (
    <div className="modal-backdrop" onMouseDown={(event) => { if (event.target === event.currentTarget) onClose?.(); }}>
      <section ref={dialogRef} className={`modal modal-${size}`} role="dialog" aria-modal="true" aria-labelledby="modal-title">
        <div className="modal-head"><div><h2 id="modal-title">{title}</h2>{description ? <p>{description}</p> : null}</div><IconButton label="Close dialog" onClick={onClose} ref={closeRef}><X size={18} /></IconButton></div>
        <div className="modal-body">{children}</div>
      </section>
    </div>
  );
}

export function ConfirmModal({ open, title, description, actionLabel = 'Confirm', danger = false, onCancel, onConfirm, busy = false }) {
  return <Modal open={open} title={title} description={description} onClose={onCancel} size="sm">
    <div className="modal-actions"><Button variant="secondary" onClick={onCancel} disabled={busy}>Cancel</Button><Button variant={danger ? 'danger' : 'primary'} onClick={onConfirm} loading={busy}>{actionLabel}</Button></div>
  </Modal>;
}

export function ToastViewport({ items, dismiss }) {
  return <div className="toast-viewport" aria-live="polite" aria-atomic="false">{items.map(item => <div key={item.id} className={`toast toast-${item.kind}`}><span>{item.kind === 'success' ? <Check size={16} /> : <AlertCircle size={16} />}</span><p>{item.message}</p><IconButton label="Dismiss notification" onClick={() => dismiss(item.id)}><X size={14} /></IconButton></div>)}</div>;
}

export function ExternalLink({ href, children }) {
  return <a href={href} target="_blank" rel="noreferrer" className="external-link">{children}<ArrowUpRight size={14} /></a>;
}
