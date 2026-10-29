import { Skel } from './Skel'
import atoms from './atoms.module.css'
import styles from './LibrarySkeleton.module.css'

function RowSkeletons() {
  return (
    <>
      {[0, 1, 2].map(i => (
        <div key={i} className={styles.row}>
          <Skel w="24%" h={11} />
          <Skel w="72%" h={16} />
          <Skel w="96%" h={11} />
          <Skel w="58%" h={11} />
        </div>
      ))}
    </>
  )
}

/** 首頁 bento 的骨架：版面與實際四張卡對齊，換入時不跳動。 */
export function HomeSkeleton() {
  return (
    <div className={`${styles.bento} ${styles.fade}`} aria-busy="true" aria-label="載入中" data-testid="home-skeleton">
      <div className={`${atoms.tile} ${styles.headline}`}>
        <Skel w="28%" h={12} />
        <Skel w="92%" h={22} />
        <Skel w="64%" h={22} />
        <Skel w="100%" h={12} style={{ marginTop: 10 }} />
        <Skel w="96%" h={12} />
        <Skel w="78%" h={12} />
        <Skel w="22%" h={12} style={{ marginTop: 18 }} />
        <Skel w="88%" h={12} />
        <Skel w="82%" h={12} />
        <Skel w="90%" h={12} />
      </div>
      <div className={`${atoms.tile} ${styles.changes}`}>
        <Skel w="32%" h={14} />
        <Skel w="100%" h={30} />
        <Skel w="100%" h={30} />
        <Skel w="100%" h={30} />
      </div>
      <div className={`${atoms.tile} ${styles.brief}`}>
        <Skel w="26%" h={14} />
        <Skel w="94%" h={12} />
        <Skel w="86%" h={12} />
        <Skel w="90%" h={12} />
      </div>
      <div className={`${atoms.tile} ${styles.latest}`}>
        <RowSkeletons />
      </div>
    </div>
  )
}

/** 結果清單的骨架（標題列＋三列）。 */
export function ListSkeleton() {
  return (
    <div className={`${styles.list} ${styles.fade}`} aria-busy="true" aria-label="搜尋中" data-testid="list-skeleton">
      <div className={styles.listHead}>
        <Skel w={90} h={16} />
        <Skel w={40} h={16} />
      </div>
      <div className={`${atoms.tile} ${styles.listTile}`}>
        <RowSkeletons />
      </div>
    </div>
  )
}
