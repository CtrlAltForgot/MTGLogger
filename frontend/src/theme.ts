import { alpha, createTheme } from '@mui/material/styles'

export function appTheme(dark:boolean){
  const accent=dark?'#FF6B73':'#C92B3D'
  const background=dark?'#0D090A':'#F8F4F4'
  const paper=dark?'#181112':'#FFFFFF'
  const border=dark?'rgba(255,255,255,.085)':'rgba(55,20,25,.10)'
  return createTheme({
    palette:{mode:dark?'dark':'light',primary:{main:accent},success:{main:dark?'#64D997':'#168A4C'},error:{main:dark?'#FF646C':'#C82737'},warning:{main:dark?'#FFB74D':'#B66B00'},background:{default:background,paper},divider:border,text:{primary:dark?'#FAF5F5':'#281719',secondary:dark?'#AE9FA1':'#746568'}},
    shape:{borderRadius:16},
    typography:{fontFamily:'-apple-system,BlinkMacSystemFont,"SF Pro Text","Segoe UI",sans-serif',h3:{fontWeight:760,letterSpacing:'-.045em'},h4:{fontWeight:760,letterSpacing:'-.035em'},h5:{fontWeight:720,letterSpacing:'-.025em'},h6:{fontWeight:700,letterSpacing:'-.018em'},button:{fontWeight:680,textTransform:'none',letterSpacing:'-.01em'}},
    components:{
      MuiCssBaseline:{styleOverrides:{body:{backgroundImage:dark?'radial-gradient(circle at 18% -10%, rgba(194,42,59,.16), transparent 31%), radial-gradient(circle at 100% 0%, rgba(115,34,45,.10), transparent 28%)':'radial-gradient(circle at 15% -10%, rgba(220,65,78,.13), transparent 31%)',backgroundAttachment:'fixed'},'::selection':{background:alpha(accent,.28)}}},
      MuiAppBar:{styleOverrides:{root:{background:alpha(dark?'#100A0B':'#FCF8F8',.8),color:dark?'#FAF5F5':'#281719',borderBottom:`1px solid ${border}`,boxShadow:'none',backdropFilter:'saturate(160%) blur(24px)',WebkitBackdropFilter:'saturate(160%) blur(24px)'}}},
      MuiCard:{styleOverrides:{root:{background:alpha(paper,dark?.88:.92),backgroundImage:'none',border:`1px solid ${border}`,boxShadow:dark?'0 12px 34px rgba(0,0,0,.24)':'0 10px 30px rgba(86,25,34,.08)',borderRadius:20}}},
      MuiPaper:{styleOverrides:{root:{backgroundImage:'none'},rounded:{borderRadius:20}}},
      MuiDialog:{styleOverrides:{paper:{background:alpha(paper,.96),border:`1px solid ${border}`,boxShadow:'0 28px 90px rgba(0,0,0,.38)',backdropFilter:'blur(28px)'}}},
      MuiBackdrop:{styleOverrides:{root:{backgroundColor:dark?'rgba(8,2,3,.74)':'rgba(48,20,24,.38)',backdropFilter:'blur(5px)'}}},
      MuiButton:{defaultProps:{disableElevation:true},styleOverrides:{root:{borderRadius:12,minHeight:40,paddingInline:16},contained:{boxShadow:`0 7px 18px ${alpha(accent,.2)}`}}},
      MuiIconButton:{styleOverrides:{root:{borderRadius:12}}},
      MuiOutlinedInput:{styleOverrides:{root:{borderRadius:14,background:alpha(dark?'#FFFFFF':'#38151B',dark?.035:.025),'& fieldset':{borderColor:border},'&:hover fieldset':{borderColor:alpha(accent,.42)},'&.Mui-focused fieldset':{borderWidth:1.5}}}},
      MuiSelect:{defaultProps:{MenuProps:{PaperProps:{sx:{mt:1,border:'1px solid',borderColor:'divider',boxShadow:'0 18px 48px rgba(0,0,0,.24)'}}}}},
      MuiChip:{styleOverrides:{root:{borderRadius:10,fontWeight:650}}},
      MuiTabs:{styleOverrides:{root:{minHeight:44},indicator:{height:3,borderRadius:'3px 3px 0 0'}}},
      MuiTab:{styleOverrides:{root:{minHeight:44,textTransform:'none',fontWeight:650,fontSize:13,padding:'10px 16px'}}},
      MuiTooltip:{defaultProps:{arrow:true}},
      MuiAlert:{styleOverrides:{root:{borderRadius:15}}},
    },
  })
}
