import { alpha, createTheme } from '@mui/material/styles'

export function appTheme(dark:boolean){
  const accent=dark?'#FF6B73':'#C92B3D'
  const background=dark?'#0D090A':'#F8F4F4'
  const paper=dark?'#181112':'#FFFFFF'
  const border=dark?'rgba(255,255,255,.085)':'rgba(55,20,25,.10)'
  return createTheme({
    palette:{mode:dark?'dark':'light',primary:{main:accent},success:{main:dark?'#64D997':'#168A4C'},error:{main:dark?'#FF646C':'#C82737'},warning:{main:dark?'#FFB74D':'#B66B00'},background:{default:background,paper},divider:border,text:{primary:dark?'#FAF5F5':'#281719',secondary:dark?'#AE9FA1':'#746568'}},
    shape:{borderRadius:12},
    typography:{fontFamily:'Beleren, Georgia, serif',h3:{fontWeight:700,letterSpacing:'-.025em'},h4:{fontWeight:700,letterSpacing:'-.02em'},h5:{fontWeight:700,letterSpacing:'-.015em'},h6:{fontWeight:700,letterSpacing:'-.01em'},button:{fontWeight:700,textTransform:'none',letterSpacing:'.005em'}},
    components:{
      MuiCssBaseline:{styleOverrides:{body:{backgroundImage:dark?'linear-gradient(135deg, rgba(114,30,42,.09), transparent 42%)':'linear-gradient(135deg, rgba(201,43,61,.07), transparent 42%)',backgroundAttachment:'fixed'},'::selection':{background:alpha(accent,.28)}}},
      MuiAppBar:{styleOverrides:{root:{background:alpha(dark?'#0D090A':'#FCF8F8',.9),color:dark?'#FAF5F5':'#281719',borderBottom:`1px solid ${border}`,boxShadow:'none',backdropFilter:'saturate(145%) blur(20px)',WebkitBackdropFilter:'saturate(145%) blur(20px)'}}},
      MuiCard:{styleOverrides:{root:{background:alpha(paper,dark?.58:.72),backgroundImage:'none',border:`1px solid ${border}`,boxShadow:'none',borderRadius:13,transition:'border-color 160ms ease, background-color 160ms ease','&:hover':{borderColor:alpha(accent,.2),backgroundColor:alpha(paper,dark?.72:.9)}}}},
      MuiPaper:{styleOverrides:{root:{backgroundImage:'none'},rounded:{borderRadius:13}}},
      MuiDialog:{styleOverrides:{paper:{background:alpha(paper,.96),border:`1px solid ${border}`,boxShadow:'0 28px 90px rgba(0,0,0,.38)',backdropFilter:'blur(28px)'}}},
      MuiBackdrop:{styleOverrides:{root:{backgroundColor:dark?'rgba(8,2,3,.74)':'rgba(48,20,24,.38)',backdropFilter:'blur(5px)'}}},
      MuiButton:{defaultProps:{disableElevation:true},styleOverrides:{root:{borderRadius:9,minHeight:38,paddingInline:15},contained:{boxShadow:'none'}}},
      MuiIconButton:{styleOverrides:{root:{borderRadius:9}}},
      MuiOutlinedInput:{styleOverrides:{root:{borderRadius:10,background:alpha(dark?'#FFFFFF':'#38151B',dark?.025:.018),'& fieldset':{borderColor:border},'&:hover fieldset':{borderColor:alpha(accent,.42)},'&.Mui-focused fieldset':{borderWidth:1.5}}}},
      MuiSelect:{defaultProps:{MenuProps:{PaperProps:{sx:{mt:1,border:'1px solid',borderColor:'divider',boxShadow:'0 18px 48px rgba(0,0,0,.24)'}}}}},
      MuiChip:{styleOverrides:{root:{borderRadius:7,fontWeight:650}}},
      MuiTabs:{styleOverrides:{root:{minHeight:44},indicator:{height:3,borderRadius:'3px 3px 0 0'}}},
      MuiTab:{styleOverrides:{root:{minHeight:44,textTransform:'none',fontWeight:650,fontSize:13,padding:'10px 16px'}}},
      MuiTooltip:{defaultProps:{arrow:true}},
      MuiAlert:{styleOverrides:{root:{borderRadius:15}}},
    },
  })
}
