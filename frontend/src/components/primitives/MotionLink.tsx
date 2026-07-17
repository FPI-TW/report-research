import { Link } from 'react-router'
import { motion } from 'motion/react'

/** react-router Link 的 Motion 版：可加 whileHover/whileTap 等手勢與 viewTransition。 */
export const MotionLink = motion.create(Link)
