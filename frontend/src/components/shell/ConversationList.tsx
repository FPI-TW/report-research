import { Link } from 'react-router'
import { Icon } from '../primitives/Icon'
import { useConversations } from '../../lib/useConversations'
import styles from './ConversationList.module.css'

export function ConversationList() {
  const { data } = useConversations()
  return (
    <>
      <div className={styles.newWrap}>
        <Link to="/ask" className={styles.newBtn}>
          <Icon name="plus" size={17} /> 新對話
        </Link>
      </div>
      <div className={styles.heading}>歷史對話</div>
      <div className={`${styles.list} tf-scroll`}>
        {(data ?? []).map((c) => (
          <Link key={c.conversation_id} to={`/ask?c=${encodeURIComponent(c.conversation_id)}`} className={styles.item}>
            {c.title}
          </Link>
        ))}
      </div>
    </>
  )
}
